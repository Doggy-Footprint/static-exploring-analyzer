"""Input parsing and output rendering (table / markdown / URL list)."""

from __future__ import annotations

import json
import re

__all__ = ["parse_input", "tier_label", "render_table", "render_md"]


def parse_input(text: str) -> list:
    text = text.strip()
    if not text:
        return []
    if text[0] in "[{":
        try:
            j = json.loads(text)
            if isinstance(j, dict):
                j = j.get("results") or j.get("urls") or []
            out = []
            for e in j:
                if isinstance(e, str):
                    out.append({"url": e, "title": ""})
                elif isinstance(e, dict) and e.get("url"):
                    out.append({"url": e["url"], "title": e.get("title", "")})
            if out:
                return out
        except ValueError:
            pass
    out, seen = [], set()
    for line in text.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line:
            continue
        title = ""
        if "|" in line:
            a, b = line.split("|", 1)
            if a.strip().startswith("http"):
                line, title = a.strip(), b.strip()
            else:
                line, title = b.strip(), a.strip()
        m = re.search(r"https?://\S+", line)
        if not m:
            continue
        u = m.group(0).rstrip(").,;\"'")
        if u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "title": title})
    return out


def tier_label(tier: str) -> str:
    """Compact column value: the blocklist tier prints as 0."""
    return "0" if tier == "block" else str(tier)


def render_table(rows: list) -> str:
    if not rows:
        return "(no sources)"
    lines = ["SCORE VERDICT T  SIGNALS                     URL"]
    for r in rows:
        sig = ",".join(r["signals"] + r["flags"])[:27]
        lines.append("%5.1f %-8s %-2s %-27s %s"
                     % (r["score"], r["verdict"], tier_label(r["tier"]), sig, r["url"]))
    citable = sum(1 for r in rows if r["verdict"] in ("PRIMARY", "SUPPORT"))
    dropped = sum(1 for r in rows if r["verdict"] in ("DROP", "BLOCKED"))
    lines.append("-- %d sources | %d citable | %d dropped" % (len(rows), citable, dropped))
    return "\n".join(lines)


def render_md(rows: list) -> str:
    out = ["| Score | Verdict | T | Signals | Source |", "|---|---|---|---|---|"]
    for r in rows:
        t = (r["title"] or r["url"])[:70].replace("|", "/")
        sig = ", ".join(r["signals"] + r["flags"]) or "-"
        out.append("| %.1f | %s | %s | %s | [%s](%s) |"
                   % (r["score"], r["verdict"], tier_label(r["tier"]), sig, t, r["url"]))
    return "\n".join(out)
