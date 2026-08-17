---
name: scored-web-search
description: Rank and filter web sources by credibility before reading them, using a deterministic scorer (domain tier + citation counts + engagement) instead of LLM judgment. Use for ANY research, literature review, market/competitor analysis, fact-gathering, or "조사해줘 / 자료 찾아줘 / 리서치 / 논문 찾아줘 / 시장조사" request where the answer depends on outside sources. Also use when the user complains that sources were low quality, unsourced, blog-tier, or treated as equally credible.
---

# Scored Web Search

조사 업무에서 **출처를 읽기 전에 걸러내는** 스킬. 신뢰도 판단을 LLM이 아니라
결정론적 스크립트가 하기 때문에 토큰을 거의 쓰지 않는다.

핵심 원칙 하나: **점수를 매기는 데 모델을 쓰지 말 것.** 도메인 등급·인용수·
발행일·star 수는 전부 코드와 무료 API로 알 수 있다. 모델은 통과한 소수의
자료를 *읽는* 데만 쓴다.

## 파이프라인

```
1. 수집  (저가 모델 서브에이전트)  → URL 목록만. 요약·분석 금지
2. 채점  (srcscore.py, 토큰 0)     → 0~100점 + 판정
3. 정독  (메인 모델)               → PRIMARY/SUPPORT만, 상위 N개 한정
4. 검증  (선택, 저가 모델)          → 주장↔근거 대조
5. 작성                            → 모든 수치에 [점수] 꼬리표
```

### 1단계 — 수집: 저가 모델에게 URL만 시킨다

`Agent` 툴로 서브에이전트를 띄우되 `model` 을 저가 모델(`haiku`, `gpt-5.6-luna`)로
지정한다. 서브에이전트에게 주는 지시는 **반드시 아래 제약을 포함**한다.

> 검색어 N개를 돌려서 나온 URL을 수집하라. 페이지를 열지 마라. 요약하지 마라.
> 평가하지 마라. 출력은 `URL | 제목` 형식 한 줄씩, 그 외 아무 말도 하지 마라.
> 40~80개를 목표로 하고 중복은 제거하라.

이게 이 스킬에서 가장 큰 절약 지점이다. 검색 결과 페이지를 메인 컨텍스트로
가져오지 않고, 서브에이전트 안에서 태워버린 뒤 URL 목록만 받는다.

여러 하위 주제가 있으면 서브에이전트를 주제별로 병렬로 띄운다.

### 2단계 — 채점: 스크립트에 넘긴다

```bash
python3 scripts/srcscore.py --in urls.txt --field ai
```

`--field` 는 인용 반감기를 정한다: `ai`(3년), `cs`(4년), `bio`/`med`(6년),
`policy`(5년), `general`(6년). AI/ML 조사에서 2019년 논문은 낡은 것이지만
의학에서는 아니다.

출력은 한 줄당 15토큰 남짓의 압축 표다:

```
SCORE VERDICT T  SIGNALS                     URL
 98.0 PRIMARY  3  cit=132.0k,y2017,published@NeurIPS  https://arxiv.org/abs/1706.03762
 77.9 SUPPORT  3  cit=120,y2026,preprint      https://arxiv.org/abs/2601.xxxxx
 46.2 SKIM     4  ★84.5k                      https://github.com/foo/bar
 14.0 DROP     6                              https://www.w3schools.com/...
  0.0 BLOCKED  0  RETRACTED                   https://...
```

판정 밴드:

| 판정 | 점수 | 이 자료로 무엇을 하는가 |
|---|---|---|
| PRIMARY | 78+ | 근거로 직접 인용. 수치·주장의 출처로 삼아도 됨 |
| SUPPORT | 62~77 | 보조 근거. 단독으로 결론을 세우지 않음 |
| SKIM | 46~61 | 교차확인용. 다른 자료가 같은 말을 할 때만 인용 |
| WEAK | 30~45 | 배경 파악용. **인용 금지** |
| DROP | <30 | 열지 않음 |
| BLOCKED | 0 | 철회 논문/스크래퍼. 절대 사용 금지 |

### 3단계 — 정독: 통과한 것만 연다

```bash
python3 scripts/srcscore.py --in urls.txt --min 62 --top 10 --format urls
```

여기서 나온 URL만 `WebFetch` 한다. **WEAK/DROP은 열지 않는다.** 열어보고
판단하는 순간 절약이 사라진다 — 그게 지금 문제 그 자체다.

기본 상한: 8~12개. 사용자가 "깊게"를 요구하면 20개까지.

PRIMARY가 3개 미만이면 자료가 부족한 것이다. 검색어를 바꿔 1단계를 한 번 더
돌린다. WEAK를 끌어올려 쓰지 않는다.

### 4단계 — 검증(선택): 주장↔근거 대조

사용자가 정확성을 특히 강조했거나, 보고서에 수치가 많이 들어갈 때만 한다.
저가 모델 서브에이전트에게 통과한 자료를 주고 **오직 이것만** 시킨다:

> 각 수치·주장에 대해: (a) 이 문서가 실제로 그 수치를 말하는가, (b) 그 수치의
> 원출처가 이 문서인가 아니면 재인용인가, (c) 표본 수·기간·측정 방법이 명시돼
> 있는가. `주장 | 지지/재인용/불일치 | 근거 위치` 형식으로만 답하라.

재인용(secondary)으로 판명되면 원출처 URL을 찾아 2단계로 되돌린다.

### 5단계 — 작성

모든 수치·주장 옆에 출처와 점수를 남긴다: `... 32% 증가했다 (Nature 2025,
PRIMARY 91)`. 점수가 SKIM 이하인 근거로 쓴 문장은 "확정되지 않았다",
"한 건의 보고에 따르면" 같은 대비 표현을 붙인다.

보고서 끝에 한 줄: `자료 62건 수집 → 11건 통과 → 9건 정독 (평균 78점)`.

## 자주 쓰는 명령

```bash
# 전체 표
python3 scripts/srcscore.py --in urls.txt

# 통과한 URL만 (다음 단계 입력)
python3 scripts/srcscore.py --in urls.txt --min 62 --format urls

# 보고서 부록용 마크다운 표
python3 scripts/srcscore.py --in urls.txt --format md

# 네트워크 없이 도메인 등급만 (즉시, 완전 무료)
python3 scripts/srcscore.py --in urls.txt --no-net

# URL 몇 개만 빠르게
python3 scripts/srcscore.py -u https://arxiv.org/abs/1706.03762 -u https://w3schools.com/x
```

조회 결과는 `~/.cache/srcscore/` 에 14일간 캐시된다. 같은 주제를 다시 조사하면
2단계는 사실상 즉시 끝난다.

## 점수는 어떻게 나오는가

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

## 점수표 손보기

`scripts/domains.json` 만 고치면 된다. 재시작 불필요.

- 특정 도메인을 신뢰하게: 해당 티어 배열에 도메인 추가
- 특정 사이트 영구 배제: `block` 배열에 추가
- 경로 단위 지정 가능: `"medium.com/towards-data-science"` 처럼 `호스트/경로`
  형태로 넣으면 더 구체적인 패턴이 우선한다

사용자가 "이 사이트는 믿을 만해 / 이건 쓰지 마"라고 하면 그 자리에서 이 파일을
고치고, 무엇을 어느 티어에 넣었는지 한 줄로 알려준다.

## 하지 말 것

- 모델에게 "이 출처들 신뢰도 평가해줘"라고 시키지 말 것. 스크립트가 한다.
- 점수를 확인하려고 페이지를 열지 말 것.
- WEAK/DROP 자료를 "그래도 참고삼아" 인용하지 말 것.
- 검색 결과 스니펫을 메인 컨텍스트에 쌓지 말 것. 서브에이전트가 URL만 반환한다.
- 도메인 점수가 높다는 이유만으로 내용을 검증 없이 신뢰하지 말 것. 등급은
  *읽을 대상을 고르는* 장치지 *사실을 보증하는* 장치가 아니다.
