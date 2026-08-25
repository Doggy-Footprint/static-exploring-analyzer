---
name: scored-web-search
description2: Filter and rank web sources by credibility before main agent reads them, using deterministic score (domain tier, citation counts, engagement, etc). LLM judgement is not included. Use for high quality research, fact-gathering requests in which sub-standard sources can compromise quality of answer. Or use it when explicitly request for this skill. If you are not sure about necesity of this skill, ask user.
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

### Step 1 — Search: collect URLs only

The main agent defines the search keywords. Delegate the actual web search to a **lightweight subagent** (do NOT use `fork`) and accept only a `URL | title` list in return. No summaries, no snippets — the point of this step is to keep low-quality text out of the main context.

Max 2 sub-topics per subagent. For 3 or more sub-topics, run subagents in parallel. Collect 40-60 URLs total.

### Step 2 — Score: hand it to the script

Write all subagent returns to `urls.txt`(one URL per line) in temporal directory and run `sciprts/srcscore.py`. You can check arguments with `--help`.

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

Fewer than 3 PRIMARY means the evidence base is insufficient. Do not promote WEAK sources to fill the gap — change the keywords and re-run step 1. Same if reading in step 3 surfaced a new sub-topic worth searching.

Two re-search rounds max. Beyond that, **ask the user explicitly** before searching again, and if they decline, state plainly in the answer that the evidence base is thin.

### Step 5 — Verify (optional): claims vs. evidence

Only when the user has stressed accuracy, or the report carries a lot of figures. Give the sources url that passed scoring filter to a **lightweight subagent** and have it do **this and nothing else**:

> For each figure or claim: (a) does this document actually state that figure, (b) is this document the original source of the figure or is it citing someone else, (c) are sample size, time period, and measurement method stated. Answer only in the format `claim | supported/secondary/contradicted | location of evidence`.

When a figure turns out to be secondary, the document you read is not its source. Take the URL of the original it cites — a new source, not yet scored — and run that through step 2; if it passes, read it and attribute the figure to it. If the original is paywalled, dead, or fails step 2, keep the figure attributed to the document you read and mark it as a re-report — never present a re-report as a primary source.

### Step 6 — Write

Tag every figure and claim with its source and score: `... rose 32% (Nature 2025, PRIMARY 91)`. Any sentence resting on a SKIM-or-below source gets a hedge — "not yet confirmed", "according to a single report".

Close the report with one line: `62 sources collected → 11 passed → 9 read (avg 78)`.

## Scoring Policy

1차(도메인 등급)로 기본 점수를 잡고, 2차(증거 지표)로 가감한다.

- **기본점**: `scripts/domains.json` 의 티어 — T1 학술지/공식통계 88, T2 정평
  있는 저널·기관 74, T3 프리프린트·주요 연구실 블로그 60, T4 전문 매체 46,
  T5 일반 매체·커뮤니티 32, T6 SEO 콘텐츠 팜 14, 차단 0. 미등록 도메인은 T5.
- **인용**: 누적 인용(로그, 최대 +20)과 인용 속도(연간, 최대 +9). OpenAlex →
  Semantic Scholar → Crossref 순으로 조회. 전부 무료·키 불필요.
- **시의성**: 분야 반감기 기준 지수 감쇠(최대 -12). 1년 미만 +4. 1000회 이상
  인용된 고전은 낡음 페널티 면제.
- **동료심사**: 프리프린트가 학회/저널에 실렸으면 +9, 아직 프리프린트면 -8.
  1년 미만인데 인용 5회 미만이면 추가 -4(`unvetted`).
- **참여도**: GitHub star(최대 +14), Hacker News 점수(최대 +8).
- **철회**: OpenAlex `is_retracted` → 즉시 0점 BLOCKED.
- **페널티**: `/best-`, `/top-10`, `/ultimate-guide` 등 SEO 경로 -6. 학술 ID가
  있는데 어느 DB에도 없으면 -5(`no-index`).

전체 수식은 `references/scoring.md` 참고.

## Don't Do This

- 모델에게 "이 출처들 신뢰도 평가해줘"라고 시키지 말 것. 스크립트가 한다.
- 점수를 확인하려고 페이지를 열지 말 것.
- WEAK/DROP 자료를 "그래도 참고삼아" 인용하지 말 것.
- 검색 결과 스니펫을 메인 컨텍스트에 쌓지 말 것. 서브에이전트가 URL만 반환한다.
- 도메인 점수가 높다는 이유만으로 내용을 검증 없이 신뢰하지 말 것. 등급은
  *읽을 대상을 고르는* 장치지 *사실을 보증하는* 장치가 아니다.
