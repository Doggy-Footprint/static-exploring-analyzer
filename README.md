# Install

```bash
clone https://github.com/Doggy-Footprint/scored-web-search

mkdir -p ~/.claude/skills
mkdir -p ~/.codex/skills

# symlink로 연결
ln -s "$(realpath scored-web-search)" ~/.claude/skills/scored-web-search
ln -s "$(realpath scored-web-search)" ~/.codex/skills/scored-web-search
```

# Customization (policy)

Check `scripts/policy.json` and `scripts/modes/` to edit this skill permanently.

---

# Scored Web Search

AI chat에서 web search를 할 때, 결과물을 heuristic한 filter로 걸러내서 신뢰도가 낮은 정보를 차단하는 skill입니다.
[posting](https://harsh-wavelength-48b.notion.site/Better-web-search-3c72e74ce621801aaec5df9464488aec?source=copy_link)

## 왜 만들었나?

> 그거 아시나요? Claude는 검색할 때, 광고도 가리지 않는다는 사실!
> 그거 아시나요? Claude는 검색한 걸 읽을 때, 모든 자료를 평등하게 바라본다는 사실!
> 그거 아시나요? 가끔 검색하면 하나 밖에 안 나오는 medium 글을 대표 사례라고 가져온다는 사실!
> 그거 아니나요? low-quality 자료가 있으면 무시하는 게 아니라 context가 오염된다는 사실!

장난스럽게 말했지만, 고질적인 RAG 문제와, Context rot, context size 문제입니다.

## 어떻게 작동하나?

1. web search는 sub agent에게 맡겨 **토큰 소모**를 줄이고, **context rot**을 방지한다. (Task/Agent 도구가 없는 환경, 예: Codex는 main agent가 직접 검색하되 snippet은 읽지 않고 URL만 추출하는 fallback으로 동작한다)
2. 출처, 인용수, 저널, 좋아요 수, 별 수 등을 바탕으로 heuristic하게 점수를 매긴다. - 자세한 내용은 `SKILL.md`, `policy` 참고.
3. main agent(고비용, 고성능)은 선별된 소스를 읽고 리포트를 작성한다.

## 사용법

최상단 **Install**을 참고해주시기 바랍니다.

## 설계 결정

1. 재검색 - 필요한 경우 main agent는 재검색을 실시할 수 있습니다. 이는 web search 중 알게된 사실을 바탕으로 검색을 확장하는 것과 같으며, 모르는 주제를 다룰 때 사용합니다.
2. 토큰 소모 감소 - 11%🔻, 메인 에이전트가 읽는 소스가 줄어들고, sub agent의 토큰 소모도 많지 않아서 더 드라마틱 감소를 기대했는데, sub agent cold-start 비용이 30k이라 감소폭이 적었다.
3. `scripts/policy.json`에 단일 의존하는 점수 게산 - 개발이 쉬워진만큼 이 도구를 쓰는 사람이 AI의 도움을 받아 직접 수정하길 기대했다.
