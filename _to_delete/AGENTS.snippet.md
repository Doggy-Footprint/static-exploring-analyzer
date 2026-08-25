# Codex / 범용 에이전트용 AGENTS.md 조각

아래를 프로젝트 루트 `AGENTS.md` 에 붙여넣는다. `SRCSCORE` 경로만 실제 설치
위치로 바꾼다.

---

## 조사 업무 규칙 (scored web search)

외부 자료가 필요한 모든 조사·리서치·시장분석 작업은 아래 순서를 따른다.
출처 신뢰도 판단은 **직접 하지 말고** 스크립트에 맡긴다.

채점기: `python3 ~/tools/scored-web-search/scripts/srcscore.py`

### 1. 수집 — URL만 모은다

검색을 돌려 URL을 모은다. 이 단계에서 페이지를 열거나 요약하지 않는다.
`urls.txt` 에 한 줄에 하나씩 `URL | 제목` 형식으로 적는다. 40~80개를 목표로 한다.

### 2. 채점 — 토큰을 쓰지 않는다

```bash
python3 ~/tools/scored-web-search/scripts/srcscore.py --in urls.txt --field ai
```

`--field`: ai(3년) / cs(4) / policy(5) / bio·med·general(6). 인용 반감기를 정한다.

### 3. 정독 — 통과한 것만 연다

```bash
python3 ~/tools/scored-web-search/scripts/srcscore.py --in urls.txt --min 62 --top 10 --format urls
```

여기 나온 URL만 읽는다. 8~12개가 기본 상한이다.
**WEAK/DROP은 열지 않는다.** 열어보고 판단하면 절약이 사라진다.

PRIMARY가 3개 미만이면 검색어를 바꿔 1단계를 다시 돌린다. WEAK를 끌어올려
쓰지 않는다.

### 4. 판정별 사용 규칙

| 판정 | 점수 | 용도 |
|---|---|---|
| PRIMARY | 78+ | 수치·주장의 직접 근거로 인용 가능 |
| SUPPORT | 62–77 | 보조 근거. 단독으로 결론을 세우지 않음 |
| SKIM | 46–61 | 다른 자료가 같은 말을 할 때만 인용 |
| WEAK | 30–45 | 배경 파악용. 인용 금지 |
| DROP / BLOCKED | <30 | 사용 금지 |

### 5. 작성

- 모든 수치 옆에 `(출처 연도, 판정 점수)` 를 남긴다.
- SKIM 이하 근거로 쓴 문장에는 "확정되지 않았다" 같은 대비 표현을 붙인다.
- 보고서 끝에 `수집 N건 → 통과 M건 → 정독 K건` 을 한 줄로 적는다.
- 재인용으로 확인되면 원출처를 찾아 2단계로 되돌린다. 못 찾으면 "재인용"이라
  명시한다.

### 점수표 수정

`~/tools/scored-web-search/scripts/domains.json` 의 티어 배열을 고친다.
사용자가 "이 사이트는 믿을 만해 / 이건 쓰지 마"라고 하면 그 자리에서 반영하고
무엇을 어느 티어에 넣었는지 한 줄로 알린다.
