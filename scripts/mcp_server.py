#!/usr/bin/env python3
"""
srcscore MCP 서버 (stdio, 표준 라이브러리만)

Claude Desktop / Codex CLI / 기타 MCP 클라이언트에 `score_sources` 툴 하나를
노출한다. 채점은 srcscore.py가 로컬에서 하므로 토큰을 쓰지 않는다.

Claude Desktop 설정 (~/Library/Application Support/Claude/claude_desktop_config.json):

  {
    "mcpServers": {
      "srcscore": {
        "command": "python3",
        "args": ["/절대경로/scored-web-search/scripts/mcp_server.py"]
      }
    }
  }

Codex CLI 설정 (~/.codex/config.toml):

  [mcp_servers.srcscore]
  command = "python3"
  args = ["/절대경로/scored-web-search/scripts/mcp_server.py"]
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import srcscore as S  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "score_sources",
        "description": (
            "URL 목록의 출처 신뢰도를 0~100점으로 채점한다. 1차로 도메인 등급, "
            "2차로 인용수·발행일·동료심사 여부·GitHub star·HN 점수를 무료 API로 "
            "조회해 반영한다. 철회된 논문은 자동 차단된다. LLM 판단을 쓰지 않으므로 "
            "빠르고 토큰을 소모하지 않는다. 자료를 읽기 '전에' 호출해서 어떤 것을 "
            "읽을지 정하는 용도다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "채점할 URL 목록",
                },
                "field": {
                    "type": "string",
                    "enum": sorted(S.FIELD_HALFLIFE),
                    "default": "ai",
                    "description": "조사 분야. 인용 반감기를 정한다.",
                },
                "min_score": {
                    "type": "number",
                    "description": "이 점수 미만은 결과에서 제외 (예: 62)",
                },
                "top": {"type": "integer", "description": "상위 N개만 반환"},
                "offline": {
                    "type": "boolean",
                    "default": False,
                    "description": "true면 외부 조회 없이 도메인 등급만 사용",
                },
            },
            "required": ["urls"],
        },
    }
]


def run_scoring(args: dict) -> str:
    urls = [u for u in (args.get("urls") or []) if isinstance(u, str) and u.strip()]
    if not urls:
        return "입력된 URL이 없습니다."
    tables = S.load_domains()
    cache = S.Cache(S.CACHE_DIR)
    field = args.get("field") or "ai"
    use_net = not bool(args.get("offline"))

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(
            ex.map(
                lambda u: S.score_one({"url": u}, tables, cache, field, use_net),
                urls,
            )
        )
    cache.flush()
    rows.sort(key=lambda r: -r["score"])
    if args.get("min_score") is not None:
        rows = [r for r in rows if r["score"] >= float(args["min_score"])]
    if args.get("top"):
        rows = rows[: int(args["top"])]
    return S.render_table(rows)


def respond(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue

        method = msg.get("method")
        mid = msg.get("id")

        if method == "initialize":
            respond(mid, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "srcscore", "version": S.VERSION},
            })
        elif method == "tools/list":
            respond(mid, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") != "score_sources":
                respond(mid, error={"code": -32601, "message": "unknown tool"})
                continue
            try:
                text = run_scoring(params.get("arguments") or {})
                respond(mid, {"content": [{"type": "text", "text": text}]})
            except Exception as e:  # 툴 오류는 결과로 돌려준다
                respond(mid, {
                    "content": [{"type": "text", "text": "채점 실패: %s" % e}],
                    "isError": True,
                })
        elif method in ("notifications/initialized", "notifications/cancelled"):
            continue
        elif mid is not None:
            respond(mid, error={"code": -32601, "message": "method not found: %s" % method})


if __name__ == "__main__":
    main()
