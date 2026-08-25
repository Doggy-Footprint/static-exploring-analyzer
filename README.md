# scored-web-search

A Claude Code skill that filters and ranks web sources by credibility
*before* the main agent reads them — using a deterministic score (domain
tier, citation counts, engagement, recency, peer-review status, etc). No LLM
judgement enters the score at any point; everything is code and free APIs.

Use it for research/fact-gathering tasks where sub-standard sources would
compromise the quality of the answer.

## How it works

```
1. Search    (lightweight subagent) → URL list only, no summaries
2. Score     (srcscore.py)          → 0-100 + verdict
3. Read      (main agent)           → top n PRIMARY/SUPPORT only
4. Re-search (loop back to 1 if sources are thin)
5. Verify    (optional)             → claims vs. evidence
6. Write     → every claim tagged with source + score
```

The full pipeline, verdict bands, and rules of use are documented in
[`SKILL.md`](SKILL.md) — that's what Claude reads when the skill is invoked.
The scoring policy itself (tier list, formulas, worked examples) lives in
[`references/scoring.md`](references/scoring.md).

## Repository layout

```
SKILL.md               skill definition Claude Code loads
references/
  scoring.md            scoring policy doc, generated from policy.json
scripts/
  srcscore.py            the scorer — CLI entry point
  check_policy.py        regenerates/validates references/scoring.md, runs golden tests
  policy.json             single source of truth: tiers, domains, formulas, bands
  golden.json             regression fixtures used by check_policy.py
  install-hooks.sh        one-time setup for the pre-commit policy check
hooks/
  pre-commit              blocks commits where policy.json and scoring.md disagree
tests/                   offline unit tests for srcscore.py and check_policy.py
```

## Using the skill

Clone (or add as a submodule / plugin) into wherever your Claude Code skills
are discovered, then invoke it — either explicitly ("use scored-web-search")
or let Claude pick it up automatically when it recognizes a high-stakes
research task described in `SKILL.md`'s `description` front matter.

## Using the scorer directly

`scripts/srcscore.py` has no dependency on Claude Code and can be run
standalone:

```bash
# score URLs from a file, one per line
python3 scripts/srcscore.py --in urls.txt

# score a couple of URLs ad hoc
python3 scripts/srcscore.py -u https://arxiv.org/abs/1706.03762 -u https://www.w3schools.com/

# only keep sources that would pass as PRIMARY/SUPPORT, JSON output
python3 scripts/srcscore.py --in urls.txt --min 62 --format json

# no network calls — domain tier only, useful offline or for quick checks
python3 scripts/srcscore.py --in urls.txt --no-net

# set the citation half-life for a slower-moving field
python3 scripts/srcscore.py --in urls.txt --field med
```

Run `python3 scripts/srcscore.py --help` for the full flag list.

Output is a compact table (roughly 15 tokens/line):

```
SCORE VERDICT T  SIGNALS                     URL
 98.0 PRIMARY  3  cit=132.0k,y2017,published@NeurIPS  https://arxiv.org/abs/1706.03762
 77.9 SUPPORT  3  cit=120,y2026,preprint      https://arxiv.org/abs/2601.xxxxx
 46.2 SKIM     4  ★84.5k                      https://github.com/foo/bar
 14.0 DROP     6                              https://www.w3schools.com/...
  0.0 BLOCKED  0  RETRACTED                   https://...
```

| Verdict | Score | Meaning |
|---|---|---|
| PRIMARY | 78+ | Cite directly |
| SUPPORT | 62-77 | Supporting evidence only |
| SKIM | 46-61 | Cross-checking only |
| WEAK | 30-45 | Background reading, don't cite |
| DROP | <30 | Do not open |
| BLOCKED | 0 | Retracted / scraper — never use |

## Tuning the policy

Every number the scorer uses lives in `scripts/policy.json`. Do not hand-edit
`references/scoring.md` — it's generated.

1. Edit `scripts/policy.json` (move a domain between tiers, change a
   half-life, shift a verdict band, etc).
2. Regenerate the docs:
   ```bash
   python3 scripts/check_policy.py --fix
   ```
3. Validate everything (docs match policy, golden regression cases pass):
   ```bash
   python3 scripts/check_policy.py
   ```
4. If the change intentionally moves scores, re-baseline the golden cases and
   review the diff before committing:
   ```bash
   python3 scripts/check_policy.py --bless
   ```

See `references/scoring.md` → "Tuning" for a worked decision tree, and
"Known limits" for what the score deliberately does not capture.

## Setup

Install the pre-commit hook once after cloning — it fails a commit if
`policy.json`, `references/scoring.md`, or the golden fixtures drift apart:

```bash
sh scripts/install-hooks.sh
```

## Tests

Offline unit tests for the scorer and the policy checker:

```bash
python3 -m pytest tests/
```
