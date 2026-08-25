# Scoring policy

Everything `scripts/srcscore.py` computes. All of it comes from code and free
APIs; no LLM judgement enters the score at any point.

```
final = clamp(0, 100, tier_base + Σ adjustments)
```

**Every number on this page lives in `scripts/policy.json`.** The tables and
formulas below sit inside `<!-- policy:... -->` markers and are generated from
that file by `scripts/check_policy.py`. Do not hand-edit them — edit
`policy.json` and run `python3 scripts/check_policy.py --fix`. The pre-commit
hook fails the commit if the two ever drift apart.

## First pass: domain tier (base score)

<!-- policy:tiers -->
| Tier | Base | Definition |
|---|---|---|
| 1 | 88 | Academic journals, official statistics, standards bodies |
| 2 | 74 | Reputable journals, major institutions and universities |
| 3 | 60 | Preprints, major research-lab and vendor engineering blogs |
| 4 | 46 | Trade media, well-known individual technical blogs |
| 5 | 32 | General media, community sites, aggregators (default for unregistered domains) |
| 6 | 14 | SEO content farms, unsourced listicles, market-research spam |
| block | 0 | Scrapers, mirrors, plagiarism hosts. Always BLOCKED |

Unregistered domains start at tier 5.
<!-- /policy:tiers -->

<!-- policy:domains -->
| Tier | Registered patterns |
|---|---|
| 1 | 44 |
| 2 | 44 |
| 3 | 54 |
| 4 | 30 |
| 5 | 38 |
| 6 | 26 |
| block | 13 |

Matching is by registered domain. The longest (most specific) pattern wins, and a `host/path` pattern always beats a host-only one - which is how `nature.com` sits at tier 1 while `nature.com/news` sits at tier 4.
<!-- /policy:domains -->

## Second pass: evidence signals (adjustments)

### Citations (academic sources only)

<!-- policy:citations -->
```
c        = cumulative citations
age      = years since publication
cum_pts  = min(20, 6.2 * log10(1 + c))
vel_pts  = min(9, 4.2 * log10(1 + c / max(0.75, age)))
```
<!-- /policy:citations -->

The `max(0.75, age)` floor matters. Without it, a preprint published yesterday
with two citations scores an annualised velocity of 700 and beats a classic.

Lookup order: OpenAlex (DOI / PMID / arXiv DOI) → Semantic Scholar (arXiv ID) →
no signal. arXiv papers are found in OpenAlex under the `10.48550/arXiv.{id}` DOI.

### Recency

<!-- policy:halflife -->
Citation half-life in years, selected with `--field` (default `ai`):

ai 3, cs 4, policy 5, bio 6, med 6, general 6
<!-- /policy:halflife -->

<!-- policy:recency -->
```
age < 1    years  -> +4
age < 2    years  -> +2
otherwise         -> -12 * (1 - 0.5^((age - 2) / halflife))
```

Classics do not rot: at 1000+ citations the decay penalty is waived entirely, and at 300+ citations only 40% of it applies.
<!-- /policy:recency -->

### Peer-review status

<!-- policy:peer-review -->
| Condition | Adjustment | Flag |
|---|---|---|
| Published in a journal or conference | +9 | `published@{venue}` |
| Still a preprint | -8 | `preprint` |
| Preprint less than 1 year(s) old with fewer than 5 citations | -4 (additional) | `unvetted` |

Applies only to preprint hosts: arxiv.org, biorxiv.org, medrxiv.org, chemrxiv.org, ssrn.com, researchsquare.com, osf.io, hal.science.
<!-- /policy:peer-review -->

Publication is decided by OpenAlex `locations[]`: a source that is a journal,
conference or book series rather than a repository. The domain alone can never
reveal this, and this lookup is the only place it is discoverable.

### Citation gaps

<!-- policy:citation-gap -->
| Condition | Adjustment | Flag |
|---|---|---|
| No citations at all, older than 2 years | -6 | `uncited` |
| Fewer than 10 citations, older than 4 years | -6 | `low-cite` |

First matching rule wins.
<!-- /policy:citation-gap -->

### Engagement (non-academic sources)

<!-- policy:engagement -->
```
gh_pts = min(14, 3.6 * log10(1 + stars))     # archived repository -4
hn_pts = min(8, 3 * log10(1 + points))
```

The Hacker News lookup only runs for tier 3 and below when the URL carries no academic identifier and no GitHub repository.
<!-- /policy:engagement -->

### Other penalties

<!-- policy:penalties -->
| Condition | Adjustment | Flag |
|---|---|---|
| URL contains a listicle path pattern (`/best-`, `/top-10`, `/top-5`, ...) | -6 | `seo-path` |
| URL carries an academic ID that no database knows, and either it isn't an arXiv id or the arXiv id decodes to 0.25+ years old | -5 | `no-index` |
| arXiv id decodes to under 0.25 years old and no database knows it yet (indexing lag, not evidence of low quality) | -8 | `no-index-recent` |

Full pattern list (11): `/best-`, `/top-10`, `/top-5`, `/top-7`, `/ultimate-guide`, `/everything-you-need-to-know`, `-in-2023`, `-in-2024`, `/what-is-`, `/complete-guide-to`, `/beginners-guide`.
<!-- /policy:penalties -->

### Hard blocks

- OpenAlex `is_retracted == true` → score 0, flag `RETRACTED`, verdict BLOCKED.
- A match in the `block` domain list → score 0, verdict BLOCKED.

Both short-circuit: no adjustment can lift a blocked source.

## Verdict bands

<!-- policy:bands -->
| Verdict | Score | What to do with it |
|---|---|---|
| PRIMARY | 78+ | Cite directly. Valid source for figures and claims |
| SUPPORT | 62-77 | Supporting evidence. Never the sole basis for a conclusion |
| SKIM | 46-61 | Cross-checking only. Cite only when another source says the same |
| WEAK | 30-45 | Background reading. Do not cite |
| DROP | 0-29 | Do not open |
| BLOCKED | 0 | Hard block: retracted paper or blocklisted host. Never use |
<!-- /policy:bands -->

## Worked examples

These are the golden regression cases in `scripts/golden.json`, scored by the
current policy. They run offline — academic cases inject a synthetic record so
the arithmetic stays deterministic — and the pre-commit hook re-runs them.

<!-- policy:examples -->
| Case | Score | Verdict |
|---|---|---|
| landmark paper, published, massively cited | 98.0 | PRIMARY |
| conference paper, moderate citations | 81.8 | PRIMARY |
| hot recent preprint | 77.9 | SUPPORT |
| brand new preprint, nobody has cited it | 52.0 | SKIM |
| old preprint that went nowhere | 40.4 | WEAK |
| retracted paper is blocked outright | 0.0 | BLOCKED |
| popular repository | 60.0 | SKIM |
| archived repository loses ground | 53.1 | SKIM |
| top-tier journal, no identifier in the URL | 88.0 | PRIMARY |
| path pattern beats host pattern | 46.0 | SKIM |
| unregistered domain falls back to the default tier | 32.0 | WEAK |
| listicle path penalty | 26.0 | DROP |
| content farm | 14.0 | DROP |
| market-research spam | 14.0 | DROP |
| blocklisted host | 0.0 | BLOCKED |
<!-- /policy:examples -->

## Tuning

When a score disagrees with your judgement, work down this list. Every step is
a `policy.json` edit; none of them touch `srcscore.py`.

1. **One site is misplaced** → move it between `domains` tier arrays. Most
   complaints end here.
2. **A whole field reads as too stale or too fresh** → pass a different
   `--field`, or change `field_halflife_years`.
3. **Preprints are over- or under-valued across the board** → change
   `peer_review.published_bonus` / `preprint_penalty`.
4. **The band edges are wrong** → move `verdicts.bands[].min`. Leaving the
   formulas alone and shifting only the thresholds is the safest change.

After any edit:

```bash
python3 scripts/check_policy.py --fix     # regenerate this page from policy.json
python3 scripts/check_policy.py           # verify docs + golden cases
```

If a change intentionally moves the golden scores, re-baseline them with
`--bless` and read the resulting diff before committing.

## Known limits

- A domain tier is a device for **choosing what to read**, not a guarantee that
  the content is **true**. A Nature paper can be wrong and a personal blog can be
  right. Step 5 of the skill (claims vs. evidence) covers that layer.
- Citation counts carry field and seniority bias. Good work in a young field
  scores low.
- Non-English primary sources (national statistics, court rulings, corporate
  filings) have no citation signal and lean entirely on the domain tier. Add
  them to `policy.json` directly.
- Self-citation and citation rings are not filtered out.
- GitHub and Hacker News numbers measure popularity, not accuracy. That is why
  their caps are set low.
- A brand-new arXiv preprint gets a short grace period (`policy.json`
  `penalties.no_index.grace`) before the no-index penalty applies at full
  strength, since arXiv ids embed a submission date that can be decoded
  without a lookup and indexing into Semantic Scholar/OpenAlex normally lags
  submission by weeks. DOI- and PMID-only sources have no embeddable date, so
  they still get the flat no-index penalty immediately - there is no
  lookup-free way to estimate their age.
