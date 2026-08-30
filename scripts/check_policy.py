#!/usr/bin/env python3
"""
check_policy - keep policy.json, the mode overlays and the scorer in agreement.

Two rule-based checks, no LLM involved:

  1. schema     policy.json (and every scripts/modes/*.json overlay merged
                onto it) loads, is structurally valid, has no duplicate
                domains and has properly ordered verdict bands.
  2. golden     scripts/golden.json is re-scored offline; scores and verdicts
                must match the recorded expectations.

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

GOLDEN_PATH = os.path.join(HERE, "golden.json")
MODES_DIR = os.path.join(HERE, "modes")


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


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify policy.json against the mode overlays and fixtures.")
    ap.add_argument("--policy", default=S.POLICY_PATH)
    ap.add_argument("--modes-dir", default=MODES_DIR)
    ap.add_argument("--golden", default=GOLDEN_PATH)
    ap.add_argument("--only", choices=["schema", "modes", "golden"], action="append", default=[])
    ap.add_argument("--bless", action="store_true",
                    help="re-baseline golden expectations (deliberate policy changes only)")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)
    only = set(a.only) or {"schema", "modes", "golden"}

    policy, problems = check_schema(a.policy)
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        return 1

    if "modes" in only:
        problems += check_modes(policy, a.modes_dir)

    try:
        with open(a.golden, "r", encoding="utf-8") as f:
            golden = json.load(f)
    except (OSError, ValueError) as e:
        print("golden: cannot read %s: %s" % (a.golden, e), file=sys.stderr)
        return 1

    if "golden" in only:
        problems += check_golden(policy, golden, a.golden, bless=a.bless)

    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        print("\ncheck_policy: FAILED (%d problem%s). "
              "`--bless` re-baselines golden cases after a deliberate policy change."
              % (len(problems), "" if len(problems) == 1 else "s"), file=sys.stderr)
        return 1

    if not a.quiet:
        print("check_policy: ok (%d domains, %d modes, %d golden cases)"
              % (sum(len(v) for v in policy["domains"].values()),
                 len([f for f in os.listdir(a.modes_dir) if f.endswith(".json")]),
                 len(golden["cases"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
