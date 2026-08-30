"""argparse wiring + main() for the srcscore CLI."""

from __future__ import annotations

import argparse
import json
import sys

from .fetchers import CACHE_DIR, STATS, Cache
from .io_format import parse_input, render_md, render_table
from .policy import (
    KNOWN_SWITCHES, PolicyError, apply_adjustments, apply_half_life_changes, apply_mode,
    apply_switches, load_policy, validate_policy,
)
from .scoring import score_many

__all__ = ["main"]

VERSION = "2.0.0"

MODES = ("academic", "non-academic", "community-opinion", "news", "official-docs")


def _flatten(groups) -> list:
    return [x for group in (groups or []) for x in group]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic source credibility scorer (zero LLM tokens).")
    ap.add_argument("--in", dest="infile", default="-", help="input file (default: stdin)")
    ap.add_argument("-u", "--url", action="append", default=[],
                    help="score this URL (repeatable)")
    ap.add_argument("--format", choices=["table", "json", "md", "urls"], default="table")
    ap.add_argument("--min", type=float, default=None, help="drop anything below this score")
    ap.add_argument("--top", type=int, default=None, help="keep only the top N")
    ap.add_argument("--field", default=None,
                    help="research field, sets the citation half-life (see policy.json)")
    ap.add_argument("--no-net", action="store_true",
                    help="domain tier only, no external lookups")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--policy", default=None, help="path to policy.json")
    ap.add_argument("--mode", choices=MODES, default="academic",
                    help="scoring profile, loaded from scripts/modes/<mode>.json "
                         "and merged onto --policy (default: academic)")
    ap.add_argument("--modes-dir", default=None, help="override scripts/modes/ location")
    ap.add_argument("--change-half-life", nargs="+", action="append", default=[],
                    metavar="FIELD:YEARS",
                    help="override a field's citation half-life, e.g. "
                         "--change-half-life ai:2 bio:5 (repeatable)")
    ap.add_argument("--adjust", nargs="+", action="append", default=[],
                    metavar="POLICY.PATH:DELTA",
                    help="add DELTA to an existing numeric policy value, e.g. "
                         "--adjust peer_review.published_bonus:+3 (repeatable)")
    ap.add_argument("--enable", nargs="+", action="append", default=[],
                    metavar="SWITCH", choices=KNOWN_SWITCHES,
                    help="turn a scoring component back on (e.g. after a mode disables it): "
                         "%s (repeatable)" % ", ".join(KNOWN_SWITCHES))
    ap.add_argument("--disable", nargs="+", action="append", default=[],
                    metavar="SWITCH", choices=KNOWN_SWITCHES,
                    help="turn off a scoring component: %s (repeatable)"
                         % ", ".join(KNOWN_SWITCHES))
    ap.add_argument("--version", action="version", version=VERSION)
    a = ap.parse_args(argv)

    try:
        policy = load_policy(a.policy)
        policy = apply_mode(policy, a.mode, a.modes_dir)
        policy = apply_half_life_changes(policy, _flatten(a.change_half_life))
        policy = apply_adjustments(policy, _flatten(a.adjust))
        policy = apply_switches(policy, _flatten(a.enable), _flatten(a.disable))
        validate_policy(policy, "merged policy (%s + --mode %s)" % (a.policy or "policy.json",
                                                                      a.mode))
    except PolicyError as e:
        print("srcscore: %s" % e, file=sys.stderr)
        return 3

    field = a.field or policy["defaults"]["field"]
    if field not in policy["field_halflife_years"]:
        print("srcscore: unknown --field %r (known: %s)"
              % (field, ", ".join(sorted(policy["field_halflife_years"]))), file=sys.stderr)
        return 2

    items = [{"url": u, "title": ""} for u in a.url]
    if not items:
        try:
            raw = sys.stdin.read() if a.infile == "-" else open(a.infile, encoding="utf-8").read()
        except OSError as e:
            print("srcscore: cannot read input: %s" % e, file=sys.stderr)
            return 2
        items = parse_input(raw)
    if not items:
        print("srcscore: no URLs in input", file=sys.stderr)
        return 2

    cache = Cache(CACHE_DIR, float(policy["defaults"].get("cache_ttl_days", 14)))
    workers = a.workers or int(policy["defaults"].get("workers", 8))
    rows = score_many(items, policy, cache, field, not a.no_net, workers)
    cache.flush()

    # A blocked or offline API would otherwise degrade every academic source to
    # its bare domain tier without saying so. Fail loudly instead.
    if STATS["failed"]:
        msg = ("srcscore: %d of %d external lookups could not be reached"
               % (STATS["failed"], STATS["lookups"]))
        if STATS["failed"] * 2 >= STATS["lookups"]:
            print("%s.\nScores would be domain-tier only and misleading. Check network "
                  "access to api.openalex.org / api.semanticscholar.org, or re-run with "
                  "--no-net to accept domain-only scoring deliberately." % msg, file=sys.stderr)
            return 4
        print("%s; those rows are flagged `lookup-failed`." % msg, file=sys.stderr)

    rows.sort(key=lambda r: -r["score"])
    if a.min is not None:
        rows = [r for r in rows if r["score"] >= a.min]
    if a.top:
        rows = rows[: a.top]

    if a.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    elif a.format == "md":
        print(render_md(rows))
    elif a.format == "urls":
        for r in rows:
            print(r["url"])
    else:
        print(render_table(rows))
    return 0
