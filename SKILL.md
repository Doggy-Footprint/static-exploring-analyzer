---
name: scored-web-search
description: Filter and rank web sources with heuristic scoring system before the main agent reads them. By using only select sources, this skill reduces context rot, improves the quality of research reports, and also reduces token usage. This skill provides academic, non-academic, community-opinion, news, and official-document modes. Triggering expressions are "scored web search", "search", "research", "investigate", "find", "look up", "back it up", "evidence", "fact check", and similar. This skill intentionally drops some of web-search results - if you need those for example, searching for more possible options, please use regular web search instead.
---

# Scored Web Search

This skill filters sub-standard sources before the main agent reads them when high quality research is on demand. 

## Pipeline

```
1. Search   (lightweight subagent) → URL list only, no summaries
2. Score    (srcscore.py)          → 0-100 + verdict
3. Read     (main agent)           → top n PRIMARY/SUPPORT only
4. Re-search (loop back to 1 if sources are thin)
5. Verify   (optional)             → claims vs. evidence
6. Write    → every claim tagged with source + score
```

When sub-agents are not available, the main agent does Steps 1 and 5 as fallback.

### Step 1 — Search: collect URLs only

The main agent defines the search keywords. Delegate the actual web search to a **lightweight subagent** (do NOT use `fork` / make subagent to call web-search tools at a single turn. / use gpt-5.6-luna or haiku-4.5) and accept only a `URL | title` list in return. NO summaries, NO snippets, DO NOT open page — the point of this step is to keep low-quality text out of the main context.

Max 2 sub-topics per subagent. For 3 or more sub-topics, run subagents in parallel. Collect 40-60 URLs total.

No sub agent fallback: main agent calls the web-search tool itself. Extract only the URLs from the results and write them straight to `urls.txt`. The caution for sub agents works same for main agent too.

### Step 2 — Score: hand it to the script

Write all subagent returns to `urls.txt`(one URL per line) in temporal directory and run `scripts/srcscore.py`. You can check arguments with `--help`.

Output is a compact table, roughly 15 tokens per line:

```
SCORE VERDICT T  SIGNALS                     URL
 98.0 PRIMARY  3  cit=132.0k,y2017,published@NeurIPS  https://arxiv.org/abs/1706.03762
 77.9 SUPPORT  3  cit=120,y2026,preprint      https://arxiv.org/abs/2601.xxxxx
 46.2 SKIM     4  ★84.5k                      https://github.com/foo/bar
 14.0 DROP     6                              https://www.w3schools.com/...
  0.0 BLOCKED  0  RETRACTED                   https://...
```

| Verdict | Score | What to do with it |
|---|---|---|
| PRIMARY | 78+ | Cite directly. Valid source for figures and claims |
| SUPPORT | 62-77 | Supporting evidence. Never the sole basis for a conclusion |
| SKIM | 46-61 | Cross-checking only. Cite only when another source says the same |
| WEAK | 30-45 | Background reading. **Do not cite** |
| DROP | <30 | Do not open |
| BLOCKED | 0 | Retracted paper / scraper. Never use |

### Step 3 — Read: open only what passed
`WebFetch` only the URLs this returns. **Do not open WEAK/DROP.** The moment you open one to judge it for yourself, the savings are gone — that is the exact problem this skill exists to solve.

Default cap: 8-12 sources. Up to 20 if the user asks for depth.

### Step 4 — Re-search: loop back to 1 when sources are thin

Do not promote sub-WEAK sources becase of low credible sources. Change the keywords and re-run from step 1. Same if reading in step 3 surfaced a new sub-topic worth searching

Two re-search rounds max. Beyond that, **ask the user explicitly** before searching again, and if they decline, state plainly in the answer that the evidence base is thin.

### Step 5 — Verify (optional): claims vs. evidence

Only when the user has stressed accuracy, or the report carries a lot of figures. Give the sources url that passed scoring filter to a **lightweight subagent** and have it do **this and nothing else**:

> For each figure or claim: (a) does this document actually state that figure, (b) is this document the original source of the figure or is it citing someone else, (c) are sample size, time period, and measurement method stated. Answer only in the format `claim | supported/secondary/contradicted | location of evidence`.

When a figure turns out to be secondary, the document you read is not its source. Take the URL of the original it cites — a new source, not yet scored — and run that through step 2; if it passes, read it and attribute the figure to it. If the original is paywalled, dead, or fails step 2, keep the figure attributed to the document you read and mark it as a re-report — never present a re-report as a primary source.

No sub agent fallback: run the same claim-vs-evidence check yourself instead of delegating it. This step doesn't reintroduce the context-pollution problem Step 1 guards against — the sources are already open and being read — so there's no quality loss from doing it inline.

### Step 6 — Write

Tag every figure and claim with its source and score: `... rose 32% (Nature 2025, PRIMARY 91)`. Any sentence resting on a SKIM-or-below source gets a hedge — "not yet confirmed", "according to a single report".

Close the report with one line: `62 sources collected → 11 passed → 9 read (avg 78)`.

## Scoring Policy

The tier (domain rating) sets the base score first; secondary indicators (citations, community-engagement, etc) adjust it up or down.

### First pass: domain tier (base score)

| Tier | Base | Definition |
|---|---|---|
| 1 | 88 | Academic journals, official statistics, standards bodies |
| 2 | 74 | Reputable journals, major institutions and universities |
| 3 | 60 | Preprints, major research-lab and vendor engineering blogs |
| 4 | 46 | Trade media, well-known individual technical blogs |
| 5 | 32 | General media, community sites, aggregators (default for unregistered domains) |
| 6 | 14 | SEO content farms, unsourced listicles, market-research spam |
| block | 0 | Scrapers, mirrors, plagiarism hosts. Always BLOCKED |

## Modes

`--mode` can be used for goal of web search

| Mode | File | What changes |
|---|---|---|
| `academic` (default) | `modes/academic.json` | Empty overlay — `policy.json` itself is the academic profile. |
| `non-academic` | `modes/non_academic.json` | GitHub repos / engineering blogs / technical postings. Engagement (stars, HN) weighted higher; HN lookup no longer restricted to tier ≤3; default field `cs`. |
| `community-opinion` | `modes/community_opinion.json` | Reddit/forum/X/HN discussion. Peer-review scoring off; engagement weighted highest; a short `opinion` half-life (0.75y) becomes the default field; a post/profile link from a known reliable expert (`trusted_people` list in the mode file) gets a flat +12 bonus. |
| `news` | `modes/news.json` | News coverage. Peer-review off; a fast `news` half-life (0.2y) with tight fresh-article windows becomes the dominant signal; engagement (HN discussion) stays on. |
| `official-docs` | `modes/official_docs.json` | Product/framework docs (docs.python.org, docs.anthropic.com, ...). Recency decay, peer-review and engagement all off — docs are evergreen and credibility rests on domain tier alone. |

## Error handling
If script fails, report it and stop; do not fall back to unscored reading.


## Don't Do This

- Don't cite WEAK/DROP material "for reference anyway."
- Don't pile search-result snippets into the main context — the subagent returns URLs only.
- Don't trust content just because its domain score is high. The tier is a device for choosing what to read, not a guarantee that the content is true.
- Don't doulbe-check socre.
