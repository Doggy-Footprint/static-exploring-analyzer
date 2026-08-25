#!/usr/bin/env python3
"""
check_policy - keep policy.json, the docs and the scorer in agreement.

Three rule-based checks, no LLM involved:

  1. schema     policy.json loads, is structurally valid, has no duplicate
                domains and has properly ordered verdict bands.
  2. docs       every `<!-- policy:NAME -->` block in references/scoring.md is
                regenerated from policy.json and must match byte for byte.
  3. golden     scripts/golden.json is re-scored offline; scores and verdicts
                must match the recorded expectations.

USAGE
-----
  python3 scripts/check_policy.py            # verify everything (exit 1 on failure)
  python3 scripts/check_policy.py --fix      # rewrite the generated doc blocks
  python3 scripts/check_policy.py --bless    # re-baseline golden expectations (implies --fix)
  python3 scripts/check_policy.py --only docs

`--fix` is safe: it only ever rewrites text between policy markers.
`--bless` is not: it accepts whatever the scorer currently produces, so run it
only after a policy change you intended, and read the diff before committing.

Installed as a pre-commit hook by scripts/install-hooks.sh.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import srcscore as S  # noqa: E402

DOC_PATH = os.path.join(ROOT, "references", "scoring.md")
GOLDEN_PATH = os.path.join(HERE, "golden.json")

MARKER_RE = r"<!-- policy:%s -->\n(.*?)\n<!-- /policy:%s -->"


# ----------------------------------------------------------------------------
# Doc block renderers - the single place doc numbers come from
# ----------------------------------------------------------------------------

def _num(x) -> str:
    f = float(x)
    return str(int(f)) if f == int(f) else ("%g" % f)


def _signed(x) -> str:
    f = float(x)
    return ("+" if f > 0 else "") + _num(f)


def render_tiers(p, _g):
    out = ["| Tier | Base | Definition |", "|---|---|---|"]
    for name, spec in p["tiers"].items():
        out.append("| %s | %s | %s |" % (name, _num(spec["base"]), spec.get("label", "")))
    out.append("")
    out.append("Unregistered domains start at tier %s." % p["defaults"]["unregistered_tier"])
    return "\n".join(out)


def render_bands(p, _g):
    bands = p["verdicts"]["bands"]
    out = ["| Verdict | Score | What to do with it |", "|---|---|---|"]
    for i, b in enumerate(bands):
        lo = _num(b["min"])
        rng = ("%s+" % lo) if i == 0 else ("%s-%s" % (lo, _num(bands[i - 1]["min"] - 1)))
        out.append("| %s | %s | %s |" % (b["name"], rng, b.get("use", "")))
    out.append("| %s | 0 | Hard block: retracted paper or blocklisted host. Never use |"
               % p["verdicts"]["blocked_name"])
    return "\n".join(out)


def render_halflife(p, _g):
    hl = p["field_halflife_years"]
    pairs = ", ".join("%s %s" % (k, _num(v)) for k, v in hl.items())
    return ("Citation half-life in years, selected with `--field` (default `%s`):\n\n%s"
            % (p["defaults"]["field"], pairs))


def render_citations(p, _g):
    c = p["citations"]
    return "\n".join([
        "```",
        "c        = cumulative citations",
        "age      = years since publication",
        "cum_pts  = min(%s, %s * log10(1 + c))" % (_num(c["cumulative"]["cap"]),
                                                   _num(c["cumulative"]["coefficient"])),
        "vel_pts  = min(%s, %s * log10(1 + c / max(%s, age)))"
        % (_num(c["velocity"]["cap"]), _num(c["velocity"]["coefficient"]),
           _num(c["velocity"]["min_age_years"])),
        "```",
    ])


def render_recency(p, _g):
    r = p["recency"]
    lines = ["```"]
    for step in r["fresh_bonuses"]:
        lines.append("age < %-4s years  -> %s" % (_num(step["max_age_years"]),
                                                  _signed(step["points"])))
    lines.append("otherwise         -> %s * (1 - 0.5^((age - %s) / halflife))"
                 % (_num(r["decay"]["max_penalty"]), _num(r["decay"]["grace_years"])))
    lines.append("```")
    lines.append("")
    lines.append("Classics do not rot: at %s+ citations the decay penalty is waived entirely, "
                 "and at %s+ citations only %s of it applies."
                 % (_num(r["classic_exemption_citations"]),
                    _num(r["classic_softening"]["citations"]),
                    "%d%%" % round(float(r["classic_softening"]["factor"]) * 100)))
    return "\n".join(lines)


def render_peer_review(p, _g):
    pr = p["peer_review"]
    uv = pr["unvetted"]
    out = ["| Condition | Adjustment | Flag |", "|---|---|---|",
           "| Published in a journal or conference | %s | `published@{venue}` |"
           % _signed(pr["published_bonus"]),
           "| Still a preprint | %s | `preprint` |" % _signed(pr["preprint_penalty"]),
           "| Preprint less than %s year(s) old with fewer than %s citations | %s (additional) | `unvetted` |"
           % (_num(uv["max_age_years"]), _num(uv["max_citations"]), _signed(uv["penalty"]))]
    out.append("")
    out.append("Applies only to preprint hosts: " + ", ".join(p["preprint_hosts"]) + ".")
    return "\n".join(out)


def render_citation_gap(p, _g):
    out = ["| Condition | Adjustment | Flag |", "|---|---|---|"]
    for rule in p["citation_gap"]:
        n = int(rule["max_citations"])
        cond = ("No citations at all" if n == 0
                else "Fewer than %d citations" % (n + 1))
        out.append("| %s, older than %s years | %s | `%s` |"
                   % (cond, _num(rule["min_age_years"]),
                      _signed(rule["penalty"]), rule["flag"]))
    out.append("")
    out.append("First matching rule wins.")
    return "\n".join(out)


def render_engagement(p, _g):
    e = p["engagement"]
    g, h = e["github"], e["hackernews"]
    return "\n".join([
        "```",
        "gh_pts = min(%s, %s * log10(1 + stars))     # archived repository %s"
        % (_num(g["cap"]), _num(g["coefficient"]), _signed(g["archived_penalty"])),
        "hn_pts = min(%s, %s * log10(1 + points))" % (_num(h["cap"]), _num(h["coefficient"])),
        "```",
        "",
        "The Hacker News lookup only runs for tier %s and below when the URL carries no "
        "academic identifier and no GitHub repository." % _num(h["min_tier"]),
    ])


def render_penalties(p, _g):
    out = ["| Condition | Adjustment | Flag |", "|---|---|---|"]
    seo = p["penalties"]["seo_path"]
    ni = p["penalties"]["no_index"]
    grace = ni.get("grace")
    out.append("| URL contains a listicle path pattern (%s, ...) | %s | `%s` |"
               % (", ".join("`%s`" % s for s in p["seo_path_patterns"][:3]),
                  _signed(seo["points"]), seo["flag"]))
    if grace:
        out.append("| URL carries an academic ID that no database knows, and either it "
                   "isn't an arXiv id or the arXiv id decodes to %s+ years old | %s | `%s` |"
                   % (_num(grace["max_age_years"]), _signed(ni["points"]), ni["flag"]))
        out.append("| arXiv id decodes to under %s years old and no database knows it yet "
                   "(indexing lag, not evidence of low quality) | %s | `%s` |"
                   % (_num(grace["max_age_years"]), _signed(grace["points"]), grace["flag"]))
    else:
        out.append("| URL carries an academic ID that no database knows | %s | `%s` |"
                   % (_signed(ni["points"]), ni["flag"]))
    out.append("")
    out.append("Full pattern list (%d): %s."
               % (len(p["seo_path_patterns"]),
                  ", ".join("`%s`" % s for s in p["seo_path_patterns"])))
    return "\n".join(out)


def render_domains(p, _g):
    out = ["| Tier | Registered patterns |", "|---|---|"]
    for name in p["tiers"]:
        out.append("| %s | %d |" % (name, len(p["domains"].get(name, []))))
    out.append("")
    out.append("Matching is by registered domain. The longest (most specific) pattern wins, "
               "and a `host/path` pattern always beats a host-only one - which is how "
               "`nature.com` sits at tier 1 while `nature.com/news` sits at tier 4.")
    return "\n".join(out)


def render_examples(p, golden):
    out = ["| Case | Score | Verdict |", "|---|---|---|"]
    for case, row in run_golden(p, golden):
        out.append("| %s | %.1f | %s |" % (case["name"], row["score"], row["verdict"]))
    return "\n".join(out)


BLOCKS = [
    ("tiers", render_tiers),
    ("bands", render_bands),
    ("halflife", render_halflife),
    ("citations", render_citations),
    ("recency", render_recency),
    ("peer-review", render_peer_review),
    ("citation-gap", render_citation_gap),
    ("engagement", render_engagement),
    ("penalties", render_penalties),
    ("domains", render_domains),
    ("examples", render_examples),
]


# ----------------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------------

def run_golden(policy, golden):
    """Score every golden case offline. Returns [(case, row), ...]."""
    cache = S.NullCache()
    out = []
    for case in golden["cases"]:
        row = S.score_one(
            {"url": case["url"], "title": ""},
            policy, cache,
            case.get("field") or policy["defaults"]["field"],
            use_net=False,
            injected=case.get("inject"),
        )
        out.append((case, row))
    return out


def check_schema(policy_path):
    try:
        policy = S.load_policy(policy_path)
    except S.PolicyError as e:
        return None, ["schema: %s" % e]
    return policy, []


def check_docs(policy, golden, doc_path, fix=False):
    try:
        with open(doc_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return ["docs: cannot read %s: %s" % (doc_path, e)]

    problems, new_text = [], text
    for name, render in BLOCKS:
        pat = re.compile(MARKER_RE % (re.escape(name), re.escape(name)), re.S)
        matches = list(pat.finditer(new_text))
        if len(matches) != 1:
            problems.append("docs: expected exactly one `<!-- policy:%s -->` block in %s, found %d"
                            % (name, os.path.relpath(doc_path, ROOT), len(matches)))
            continue
        want = render(policy, golden).rstrip()
        have = matches[0].group(1).rstrip()
        if have == want:
            continue
        if fix:
            new_text = (new_text[:matches[0].start(1)] + want + new_text[matches[0].end(1):])
        else:
            diff = "\n".join(difflib.unified_diff(
                have.splitlines(), want.splitlines(),
                fromfile="scoring.md:%s" % name, tofile="policy.json:%s" % name, lineterm=""))
            problems.append("docs: block `%s` is out of date\n%s" % (name, diff))

    if fix and new_text != text:
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        print("check_policy: regenerated blocks in %s" % os.path.relpath(doc_path, ROOT))
    return problems


def check_golden(policy, golden, golden_path, bless=False):
    problems, changed = [], False
    for case, row in run_golden(policy, golden):
        exp = case.setdefault("expect", {})
        got = {"score": row["score"], "verdict": row["verdict"], "tier": row["tier"]}
        if bless:
            if exp != got:
                changed = True
                case["expect"] = got
            continue
        for key, want in exp.items():
            if got.get(key) != want:
                problems.append("golden: %s -> %s expected %s=%r, got %r"
                                % (case["name"], case["url"], key, want, got.get(key)))
    if bless and changed:
        with open(golden_path, "w", encoding="utf-8") as f:
            json.dump(golden, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("check_policy: re-baselined %s" % os.path.relpath(golden_path, ROOT))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify policy.json against the docs and fixtures.")
    ap.add_argument("--policy", default=S.POLICY_PATH)
    ap.add_argument("--doc", default=DOC_PATH)
    ap.add_argument("--golden", default=GOLDEN_PATH)
    ap.add_argument("--only", choices=["schema", "docs", "golden"], action="append", default=[])
    ap.add_argument("--fix", action="store_true", help="rewrite generated doc blocks")
    ap.add_argument("--bless", action="store_true",
                    help="re-baseline golden expectations (implies --fix)")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)
    only = set(a.only) or {"schema", "docs", "golden"}

    policy, problems = check_schema(a.policy)
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        return 1

    try:
        with open(a.golden, "r", encoding="utf-8") as f:
            golden = json.load(f)
    except (OSError, ValueError) as e:
        print("golden: cannot read %s: %s" % (a.golden, e), file=sys.stderr)
        return 1

    if "golden" in only:
        problems += check_golden(policy, golden, a.golden, bless=a.bless)
    # --bless always moves the generated examples table, so it implies --fix.
    if "docs" in only:
        problems += check_docs(policy, golden, a.doc, fix=a.fix or a.bless)

    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        print("\ncheck_policy: FAILED (%d problem%s). "
              "`--fix` regenerates doc blocks; `--bless` re-baselines golden cases."
              % (len(problems), "" if len(problems) == 1 else "s"), file=sys.stderr)
        return 1

    if not a.quiet:
        print("check_policy: ok (%d domains, %d golden cases)"
              % (sum(len(v) for v in policy["domains"].values()), len(golden["cases"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
