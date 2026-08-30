#!/usr/bin/env python3
"""
check_policy - keep policy.json, the mode overlays and the scorer in agreement.

Three rule-based checks, no LLM involved:

  1. schema     policy.json (and every scripts/modes/*.json overlay merged
                onto it) loads, is structurally valid, has no duplicate
                domains and has properly ordered verdict bands.
  2. golden     Every scripts/golden/<mode>.json file is re-scored offline
                under its matching --mode overlay; scores and verdicts must
                match the recorded expectations.
  3. coverage   Every scripts/golden/<mode>.json file must contain at least
                one case for every verdict band that mode's policy defines
                (PRIMARY/SUPPORT/SKIM/WEAK/DROP/BLOCKED) - so a mode overlay
                can't silently make some verdict unreachable.

USAGE
-----
  python3 scripts/check_policy.py            # verify everything (exit 1 on failure)
  python3 scripts/check_policy.py --bless    # re-baseline golden expectations
  python3 scripts/check_policy.py --only golden

`--bless` is not safe by default: it accepts whatever the scorer currently
produces, so run it only after a policy change you intended, and read the
diff before committing.

Installed as a pre-commit hook by scripts/install-hooks.sh.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import srcscore as S  # noqa: E402

GOLDEN_DIR = os.path.join(HERE, "golden")
GOLDEN_PATH = os.path.join(GOLDEN_DIR, "academic.json")  # default/base golden set
MODES_DIR = os.path.join(HERE, "modes")


def mode_for_golden_file(fname: str) -> str:
    """scripts/golden/non_academic.json -> mode name "non-academic", same
    filename convention as scripts/modes/*.json (see load_mode_overlay)."""
    return fname[: -len(".json")].replace("_", "-")


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


def check_modes(policy, modes_dir):
    """Every mode overlay must merge onto the base policy and still validate."""
    problems = []
    if not os.path.isdir(modes_dir):
        return ["modes: no such directory %s" % modes_dir]
    for fname in sorted(os.listdir(modes_dir)):
        if not fname.endswith(".json"):
            continue
        mode = fname[: -len(".json")]
        path = os.path.join(modes_dir, fname)
        try:
            merged = S.apply_mode(policy, mode, modes_dir)
            S.validate_policy(merged, "modes/%s" % fname)
        except S.PolicyError as e:
            problems.append("modes: %s" % e)
        except OSError as e:
            problems.append("modes: cannot read %s: %s" % (path, e))
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


def check_verdict_coverage(mode, golden, policy):
    """Every mode's golden file must exercise every verdict that mode's
    merged policy can produce, so a mode change can't quietly make a band
    unreachable without a test noticing."""
    want = {b["name"] for b in policy["verdicts"]["bands"]} | {S.blocked_name(policy)}
    got = {case["expect"]["verdict"] for case in golden["cases"] if "expect" in case}
    missing = want - got
    if missing:
        return ["coverage: mode %r golden set has no case for verdict(s) %s"
                % (mode, ", ".join(sorted(missing)))]
    return []


def iter_golden_files(golden_dir):
    """Yield (mode, path) for every scripts/golden/<mode>.json file, sorted
    by filename."""
    if not os.path.isdir(golden_dir):
        return
    for fname in sorted(os.listdir(golden_dir)):
        if fname.endswith(".json"):
            yield mode_for_golden_file(fname), os.path.join(golden_dir, fname)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify policy.json against the mode overlays and fixtures.")
    ap.add_argument("--policy", default=S.POLICY_PATH)
    ap.add_argument("--modes-dir", default=MODES_DIR)
    ap.add_argument("--golden-dir", default=GOLDEN_DIR,
                    help="directory of scripts/golden/<mode>.json fixture files")
    ap.add_argument("--only", choices=["schema", "modes", "golden"], action="append", default=[])
    ap.add_argument("--bless", action="store_true",
                    help="re-baseline golden expectations (deliberate policy changes only)")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)
    only = set(a.only) or {"schema", "modes", "golden"}

    base_policy, problems = check_schema(a.policy)
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        return 1

    if "modes" in only:
        problems += check_modes(base_policy, a.modes_dir)

    total_cases = 0
    if "golden" in only:
        files = list(iter_golden_files(a.golden_dir))
        if not files:
            print("golden: no fixture files under %s" % a.golden_dir, file=sys.stderr)
            return 1
        for mode, path in files:
            try:
                merged = S.apply_mode(base_policy, mode, a.modes_dir)
            except S.PolicyError as e:
                problems.append("golden: %s" % e)
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    golden = json.load(f)
            except (OSError, ValueError) as e:
                problems.append("golden: cannot read %s: %s" % (path, e))
                continue
            total_cases += len(golden["cases"])
            problems += check_golden(merged, golden, path, bless=a.bless)
            if not a.bless:
                problems += check_verdict_coverage(mode, golden, merged)

    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        print("\ncheck_policy: FAILED (%d problem%s). "
              "`--bless` re-baselines golden cases after a deliberate policy change."
              % (len(problems), "" if len(problems) == 1 else "s"), file=sys.stderr)
        return 1

    if not a.quiet:
        print("check_policy: ok (%d domains, %d modes, %d golden cases)"
              % (sum(len(v) for v in base_policy["domains"].values()),
                 len([f for f in os.listdir(a.modes_dir) if f.endswith(".json")]),
                 total_cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
