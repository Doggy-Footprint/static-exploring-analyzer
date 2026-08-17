# 채점 수식 명세

`srcscore.py` 가 계산하는 방식 전체. 모든 값은 코드와 무료 API에서만 나오며
LLM은 이 과정에 전혀 관여하지 않는다.

```
final = clamp(0, 100, TIER_BASE + Σ adjustments)
```

## 1차: 도메인 티어 (기본점)

| 티어 | 기본점 | 정의 |
|---|---|---|
| 1 | 88 | 1차 자료, 최상위 피어리뷰, 공식 통계·표준 (Nature, IEEE, 통계청, RFC) |
| 2 | 74 | 정평 있는 저널·학회, 주요 기관·대학 공식 발표 |
| 3 | 60 | 프리프린트, 주요 연구실/기업 공식 블로그·기술문서 |
| 4 | 46 | 양질의 전문 매체, 이름 있는 개인 기술 블로그 |
| 5 | 32 | 일반 매체, 커뮤니티, 집계 사이트. **미등록 도메인 기본값** |
| 6 | 14 | SEO 콘텐츠 팜, 출처 없는 리스티클, 시장조사 스팸 |
| block | 0 | 스크래퍼·미러·표절 사이트. 즉시 BLOCKED |

매칭 규칙: 등록 도메인 기준. 더 긴(구체적인) 패턴이 우선하고, `호스트/경로`
형태는 호스트-only 패턴을 항상 이긴다. 그래서 `medium.com` 은 T5지만
`medium.com/towards-data-science` 를 T4로 따로 올릴 수 있다.

## 2차: 증거 지표 (가감점)

### 인용 (학술 자료만)

```
c   = 누적 인용수
age = max(0, 지금 - 발행일)         # publication_date 우선, 없으면 연도
cite_pts = min(20, 6.2 · log10(1 + c))
vel      = c / max(0.75, age)
vel_pts  = min(9,  4.2 · log10(1 + vel))
```

`max(0.75, age)` 의 하한이 중요하다. 하한이 없으면 어제 나온 인용 2회짜리
프리프린트의 "연간 인용 속도"가 700회로 계산되어 고전 논문을 이긴다.

조회 순서: OpenAlex(DOI/PMID/arXiv-DOI) → Semantic Scholar(arXiv ID) →
없으면 지표 없음. arXiv는 `10.48550/arXiv.{id}` DOI로 OpenAlex에서 찾는다.

### 시의성

```
hl   = 분야 반감기 (ai 3.0 / cs 4.0 / policy 5.0 / bio·med·general 6.0)
age < 1년   → +4
age < 2년   → +2
그 외       → -12 · (1 - 0.5^((age-2)/hl))
```

고인용 보정: `c ≥ 1000` 이면 낡음 페널티 면제(고전은 낡지 않는다),
`c ≥ 300` 이면 페널티 40%만 적용.

### 동료심사 상태

프리프린트 호스트(arxiv, biorxiv, medrxiv, SSRN, OSF …)에 대해서만 적용:

| 조건 | 가감 | 플래그 |
|---|---|---|
| 학회/저널에 정식 게재됨 | +9 | `published@{venue}` |
| 아직 프리프린트 | -8 | `preprint` |
| 프리프린트 + 1년 미만 + 인용 5회 미만 | 추가 -4 | `unvetted` |

게재 여부는 OpenAlex `locations[]` 에 repository가 아닌 source(journal /
conference / book series)가 있는지로 판정한다. 도메인만 봐서는 절대 알 수 없는
정보이고, 이 조회가 그걸 알아내는 유일한 지점이다.

### 인용 공백 페널티

| 조건 | 가감 | 플래그 |
|---|---|---|
| 인용 0 + 2년 초과 | -6 | `uncited` |
| 인용 10 미만 + 4년 초과 | -6 | `low-cite` |

### 참여도 (비학술 자료)

```
gh_pts = min(14, 3.6 · log10(1 + stars))     # 아카이브된 저장소 -4
hn_pts = min(8,  3.0 · log10(1 + points))    # Hacker News Algolia API
```

Hacker News 조회는 학술 ID도 GitHub 저장소도 아닌 T3 이하 URL에만 돈다.

### 하드 차단

- OpenAlex `is_retracted == true` → 0점, `RETRACTED`, 즉시 BLOCKED
- `domains.json` 의 `block` 배열 매치 → 0점, BLOCKED

### 기타 페널티

| 조건 | 가감 | 플래그 |
|---|---|---|
| URL에 `/best-`, `/top-10`, `/ultimate-guide` 등 | -6 | `seo-path` |
| 학술 ID가 있는데 어느 DB에도 없음 | -5 | `no-index` |

## 판정 밴드

| 점수 | 판정 |
|---|---|
| 78+ | PRIMARY |
| 62–77 | SUPPORT |
| 46–61 | SKIM |
| 30–45 | WEAK |
| 0–29 | DROP |
| 하드 차단 | BLOCKED |

## 실제 채점 예시

| 자료 | 계산 | 결과 |
|---|---|---|
| Attention Is All You Need (2017, 13.2만 인용, NeurIPS 게재) | 60 +20 +9 +0 +9 | **98 PRIMARY** |
| 2022년 학회 논문, 60인용 | 60 +11 +4.5 -4.4 +9 | **80 PRIMARY** |
| 2026년 화제 프리프린트, 120인용 | 60 +12.9 +9 +4 -8 | **78 SUPPORT** |
| 갓 나온 0인용 프리프린트 | 60 +0 +0 +4 -8 -4 | **52 SKIM** |
| 2013년 5인용 프리프린트 | 60 +4.8 +0.6 -11.1 -8 -6 | **40 WEAK** |
| pytorch/pytorch (★9.2만) | 46 +18 → cap | **60 SKIM** |
| w3schools 튜토리얼 | 14 | **14 DROP** |
| grandviewresearch 시장 리포트 | 14 | **14 DROP** |
| Scribd 업로드 | block | **0 BLOCKED** |

## 조정 지침

점수가 체감과 어긋나면 이 순서로 손댄다.

1. **개별 사이트가 틀렸다** → `domains.json` 티어 배열만 수정. 대부분 여기서 끝난다.
2. **분야 전체가 낡게/새롭게 평가된다** → `--field` 를 바꾸거나 `FIELD_HALFLIFE` 수정.
3. **프리프린트가 전반적으로 과대/과소평가된다** → `score_one()` 의 프리프린트
   가감(-8/+9) 수정.
4. **밴드 경계가 안 맞는다** → `VERDICT_BANDS` 수정. 수식은 그대로 두고 임계값만
   옮기는 게 가장 안전하다.

## 알려진 한계

- 도메인 등급은 **읽을 대상을 고르는** 장치지 **사실을 보증하는** 장치가 아니다.
  Nature 논문도 틀릴 수 있고 개인 블로그가 맞을 수 있다. 4단계(주장↔근거 대조)가
  그 층을 담당한다.
- 인용수는 분야·연차 편향이 있다. 신생 분야의 좋은 논문이 저평가된다.
- 비영어권 1차 자료(국내 통계·판례·기업 공시)는 인용 지표가 없어 도메인 등급에만
  의존한다. `domains.json` 에 직접 추가해서 보완한다.
- 자기인용·인용 링(citation ring)은 걸러내지 않는다.
- HN/GitHub 지표는 인기지 정확성이 아니다. 상한(+8/+14)을 낮게 잡은 이유다.
