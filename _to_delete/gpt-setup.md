# GPT에서 같은 파이프라인 쓰기

`srcscore.py` 는 모델과 무관한 CLI라서 GPT 쪽에서도 그대로 쓸 수 있다. 문제는
"GPT가 내 맥북에서 이 스크립트를 실행할 수 있는가"뿐이고, 경로가 셋 있다.

## 경로 A — Codex CLI (권장)

Codex CLI는 ChatGPT 계정으로 로그인해서 **구독 사용량**으로 돌아가고, 로컬
셸을 그대로 쓴다. 조사 파이프라인 전체가 맥북 안에서 끝난다.

```bash
npm i -g @openai/codex
codex            # 첫 실행 시 "Sign in with ChatGPT" 선택
```

프로젝트 폴더에 `AGENTS.md` 를 두면 Codex가 매 세션 자동으로 읽는다.
`references/AGENTS.snippet.md` 의 내용을 거기에 붙여넣으면 된다.

모델을 싸게 고정하려면 `~/.codex/config.toml`:

```toml
model = "gpt-5.1-codex-mini"     # 수집·채점용 저가 모델
model_reasoning_effort = "low"
```

깊은 분석이 필요한 순간에만 세션 안에서 `/model` 로 올린다. 수집 단계는 어차피
URL만 모으는 일이라 저가 모델로 충분하다.

MCP 서버로 붙이고 싶으면 `~/.codex/config.toml` 에:

```toml
[mcp_servers.srcscore]
command = "python3"
args = ["/Users/USERNAME/tools/scored-web-search/scripts/mcp_server.py"]
```

## 경로 B — ChatGPT 앱 + 개발자 모드 MCP

ChatGPT 설정 → Connectors → Advanced → Developer mode 를 켜면 MCP 커넥터를
직접 등록할 수 있다. 다만 ChatGPT는 **원격(HTTP/SSE) MCP만** 받는다. 로컬
stdio 서버인 `mcp_server.py` 를 붙이려면 터널이 하나 필요하다.

```bash
# stdio → HTTP 브리지
npx -y supergateway --stdio "python3 /Users/USERNAME/tools/scored-web-search/scripts/mcp_server.py" --port 8787
# 외부 노출
npx -y localtunnel --port 8787      # 또는 cloudflared / ngrok
```

나온 https URL을 ChatGPT 커넥터에 등록한다. 맥북이 켜져 있어야 하고 URL이
공개로 뜨니 조사용 채점기 하나만 노출하는 지금 구성에서만 권한다. 상시로 쓸
거면 경로 A가 낫다.

## 경로 C — 실행 없이, 지시문만 (성능 저하)

로컬 실행을 못 붙이는 상황(모바일 ChatGPT 등)의 차선책. 도메인 등급을 모델
머릿속에서 적용시키는 방식이라 토큰을 더 쓰고 인용수 보정이 빠진다. 그래도
"콘텐츠 팜과 학술지를 같이 취급"하는 문제는 상당히 잡힌다.

Custom Instructions 나 프로젝트 지시문에 붙여넣을 것:

> 조사 업무에서 출처를 인용하기 전에 아래를 적용한다.
>
> **1단계 — 도메인 등급.** 각 출처를 T1~T6으로 분류한다.
> T1(88점) 학술지·공식통계·표준문서(nature, science, IEEE, ACM, JMLR, 통계청,
> RFC, SEC). T2(74) 정평 있는 저널·주요 기관·대학 공식 발표. T3(60) 프리프린트
> (arXiv, bioRxiv)·주요 연구실 공식 블로그·공식 기술문서. T4(46) 전문 매체
> (Ars Technica, MIT Tech Review, FT)·이름 있는 개인 기술 블로그. T5(32) 일반
> 매체·커뮤니티·Medium·Wikipedia·SNS. T6(14) SEO 콘텐츠 팜(w3schools,
> geeksforgeeks, simplilearn)·출처 없는 리스티클·시장조사 스팸(grandview,
> marketsandmarkets). 목록에 없는 도메인은 T5로 시작한다.
>
> **2단계 — 증거 보정.** arXiv/논문이면 인용수와 발행연도를 검색으로 확인해서
> 조정한다. 인용 1000회 이상 +15, 100회 이상 +10, 10회 미만 -6. AI/ML은 3년
> 넘으면 -5, 6년 넘으면 -10 (단 인용 1000회 이상은 면제). 학회·저널에 정식
> 게재됐으면 +9, 아직 프리프린트면 -8. GitHub 저장소는 star 1만 이상 +12,
> 1천 이상 +7. 철회된 논문은 즉시 배제한다.
>
> **3단계 — 사용 규칙.** 78점 이상만 수치·주장의 직접 근거로 인용한다.
> 62~77은 보조 근거. 46~61은 다른 자료가 같은 말을 할 때만. 46 미만은 배경
> 파악용이며 **인용하지 않는다**. 시장 규모·성장률 수치는 T1/T2 출처가 없으면
> "신뢰할 만한 출처를 찾지 못했다"고 명시하고 숫자를 쓰지 않는다.
>
> **4단계 — 표기.** 모든 수치 옆에 `(출처명 연도, 등급 점수)` 를 남기고,
> 보고서 끝에 `수집 N건 → 통과 M건` 을 적는다. 재인용(2차 인용)으로 확인된
> 수치는 원출처를 찾아 교체하고, 못 찾으면 "재인용"이라고 밝힌다.

## 세 경로 비교

| | 실행 위치 | 인용수 보정 | 토큰 비용 | 맥북 필요 |
|---|---|---|---|---|
| A. Codex CLI | 로컬 셸 | 있음 (정확) | 최소 | 예 |
| B. ChatGPT + MCP | 로컬 + 터널 | 있음 (정확) | 최소 | 예 (터널 유지) |
| C. 지시문만 | 없음 | 모델 추정 | 큼 | 아니오 |
