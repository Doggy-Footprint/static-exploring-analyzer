# 설치와 연결

의존성 없음. macOS 기본 `python3`(3.9+)로 그냥 돈다. pip 설치 불필요.

```bash
# 원하는 곳에 풀어두기
mkdir -p ~/tools && cd ~/tools
# (scored-web-search 폴더를 여기에 둔다)

python3 ~/tools/scored-web-search/scripts/srcscore.py -u https://arxiv.org/abs/1706.03762
```

한 번은 네트워크가 되는 상태에서 돌려보고 `cit=...` 신호가 붙는지 확인한다.
안 붙으면 방화벽이 `api.openalex.org` / `api.semanticscholar.org` 를 막고 있는
것이다. 그 경우에도 `--no-net` 으로 도메인 등급 채점은 그대로 쓸 수 있다.

선택: OpenAlex는 메일 주소를 넣으면 더 넉넉한 폴링 풀로 보내준다.

```bash
export SRCSCORE_MAILTO="you@example.com"   # ~/.zshrc 에 추가
```

## 환경변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `SRCSCORE_MAILTO` | (없음) | OpenAlex polite pool |
| `SRCSCORE_DOMAINS` | `scripts/domains.json` | 다른 점수표 파일 지정 |
| `SRCSCORE_CACHE` | `~/.cache/srcscore` | 캐시 위치 |

---

## Claude Code / Cowork — 스킬로 설치

`.skill` 파일을 받아 설치하면 끝이다. 조사 성격의 요청에서 자동으로 뜨고,
`/scored-web-search` 로 직접 부를 수도 있다.

스킬이 스크립트를 부를 때는 스킬 폴더 기준 상대경로(`scripts/srcscore.py`)를
쓴다. 별도 설정이 필요 없다.

## Claude Desktop — MCP 서버로 연결 (선택)

스킬 대신, 또는 스킬과 함께 툴로 노출하고 싶을 때.

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "srcscore": {
      "command": "python3",
      "args": ["/Users/USERNAME/tools/scored-web-search/scripts/mcp_server.py"],
      "env": { "SRCSCORE_MAILTO": "you@example.com" }
    }
  }
}
```

앱 재시작 후 `score_sources` 툴이 보이면 성공이다. 이렇게 붙여두면 Cowork
세션이 클라우드에서 돌더라도 데스크톱 브리지를 통해 맥북의 이 툴을 부를 수 있다.

## Cowork 클라우드 세션에서 쓸 때

Cowork 세션 컨테이너는 학술 API로 나가는 아웃바운드가 막혀 있을 수 있다.
그럴 땐 두 가지 선택지가 있다.

1. `device_bash` 로 맥북에서 스크립트를 돌린다 (권장). 데스크톱 앱이 켜져 있으면
   바로 된다.
2. 컨테이너 안에서는 `--no-net` 으로 도메인 등급만 쓴다. 인용수 보정은 빠지지만
   콘텐츠 팜과 학술지를 가르는 1차 필터는 그대로 작동한다.
