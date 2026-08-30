"""Scoring - every number below comes out of the merged policy dict."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

from .fetchers import FETCH_FAILED, arxiv_lookup, github_repo, hn_points, openalex_by_doi, \
    openalex_by_pmid
from .identifiers import arxiv_id_age_years, extract_ids, extract_person_handle
from .policy import blocked_name, signal_enabled, tier_base, verdict_for
from .util import age_years, host_matches, host_path, human, norm_host

__all__ = [
    "match_tier", "citation_points", "recency_points", "engagement_points",
    "trusted_person_points", "score_one", "score_many",
]


def match_tier(url: str, policy: dict):
    """Return (tier_name, matched_pattern). Longer patterns win; a
    `host/path` pattern always beats a host-only one."""
    hp = host_path(url).lower()
    host = norm_host(url)
    best = None  # (specificity, tier_name, pattern)
    for tier_name, patterns in policy["domains"].items():
        for pat in patterns:
            p = str(pat).lower()
            if "/" in p:
                if hp.startswith(p) or ("/" + p) in ("/" + hp):
                    cand = (len(p) + 100, tier_name, pat)
                else:
                    continue
            elif host_matches(host, p):
                cand = (len(p), tier_name, pat)
            else:
                continue
            if best is None or cand[0] > best[0]:
                best = cand
    if best is None:
        return str(policy["defaults"]["unregistered_tier"]), None
    return best[1], best[2]


def citation_points(policy: dict, citations: int, age: float):
    """(cumulative points, velocity points)"""
    cfg = policy["citations"]
    c = max(0, int(citations or 0))
    cum = cfg["cumulative"]
    vel = cfg["velocity"]
    a = max(float(vel["min_age_years"]), age)
    cum_pts = min(float(cum["cap"]), float(cum["coefficient"]) * math.log10(1 + c))
    vel_pts = min(float(vel["cap"]), float(vel["coefficient"]) * math.log10(1 + c / a))
    return cum_pts, vel_pts


def recency_points(policy: dict, age: float, field: str, citations: int) -> float:
    if not signal_enabled(policy, "recency_decay"):
        return 0.0
    cfg = policy["recency"]
    a = max(0.0, age)
    for step in cfg["fresh_bonuses"]:
        if a < float(step["max_age_years"]):
            return float(step["points"])
    hl = float(policy["field_halflife_years"].get(
        field, policy["field_halflife_years"][policy["defaults"]["field"]]))
    grace = float(cfg["decay"]["grace_years"])
    decay = float(cfg["decay"]["max_penalty"]) * (1 - 0.5 ** ((a - grace) / hl))
    c = int(citations or 0)
    if c >= int(cfg["classic_exemption_citations"]):
        return max(decay, 0.0)
    soft = cfg["classic_softening"]
    if c >= int(soft["citations"]):
        decay *= float(soft["factor"])
    return decay


def engagement_points(policy: dict, gh, hn):
    cfg = policy["engagement"]
    pts, notes = 0.0, []
    if gh:
        g = cfg["github"]
        s = gh.get("stars", 0)
        pts += min(float(g["cap"]), float(g["coefficient"]) * math.log10(1 + s))
        notes.append("★%s" % human(s))
        if gh.get("archived"):
            pts += float(g["archived_penalty"])
            notes.append("archived")
    if hn:
        h = cfg["hackernews"]
        p = hn.get("points", 0)
        pts += min(float(h["cap"]), float(h["coefficient"]) * math.log10(1 + p))
        notes.append("HN%d" % p)
    return pts, notes


def trusted_person_points(policy: dict, url: str):
    """Bonus for a URL that is the profile/post of a known reliable person,
    per `policy["trusted_people"]` (community-opinion mode only - see
    modes/community_opinion.json). Handle matching is case-insensitive."""
    tp = policy.get("trusted_people")
    if not tp:
        return 0.0, []
    person = extract_person_handle(url)
    if not person:
        return 0.0, []
    host, handle = person
    handles = {h.lower() for h in tp.get("hosts", {}).get(host, [])}
    if handle not in handles:
        return 0.0, []
    return float(tp["bonus"]), ["trusted:%s" % handle]


def _result(item, score, verdict, tier, pat, flags, meta, notes=None):
    return {
        "url": item["url"],
        "title": meta.get("title") or item.get("title") or "",
        "score": round(score, 1),
        "verdict": verdict,
        "tier": tier,
        "matched": pat,
        "signals": notes or [],
        "flags": flags,
        "meta": meta,
    }


def score_one(item: dict, policy: dict, cache, field: str,
              use_net: bool, injected: dict = None) -> dict:
    """Score a single URL.

    `injected` lets a caller supply {"scholar": {...}, "github": {...},
    "hn": {...}} instead of hitting the network. The golden regression suite
    uses it to test the citation math deterministically and offline.
    """
    url = item["url"]
    tier, pat = match_tier(url, policy)
    notes, flags, meta = [], [], {}
    adj = 0.0

    if tier == "block":
        return _result(item, 0.0, blocked_name(policy), tier, pat, ["blocklist"], meta)

    base = tier_base(policy, tier)
    host = norm_host(url)
    is_preprint_host = any(host_matches(host, p) for p in policy["preprint_hosts"])
    peer_review_on = signal_enabled(policy, "peer_review")
    engagement_on = signal_enabled(policy, "engagement")

    # SEO path penalty
    low = url.lower()
    seo = policy["penalties"]["seo_path"]
    for frag in policy["seo_path_patterns"]:
        if frag in low:
            adj += float(seo["points"])
            flags.append(seo["flag"])
            break

    ids = extract_ids(url)
    injected = injected or {}
    sch = injected.get("scholar")
    gh = injected.get("github")
    hn = injected.get("hn")
    timeout = float(policy["defaults"].get("http_timeout_seconds", 12))

    if use_net:
        if sch is None:
            if "arxiv" in ids:
                sch = arxiv_lookup(ids["arxiv"], cache, timeout)
            elif "doi" in ids:
                sch = openalex_by_doi(ids["doi"], cache, timeout)
            elif "pmid" in ids:
                sch = openalex_by_pmid(ids["pmid"], cache, timeout)
        if engagement_on and gh is None and "github" in ids:
            gh = github_repo(ids["github"][0], ids["github"][1], cache, timeout)
        min_tier = int(policy["engagement"]["hackernews"]["min_tier"])
        if (engagement_on and hn is None and sch is None and gh is None
                and tier.isdigit() and int(tier) >= min_tier):
            hn = hn_points(url, cache, timeout)
        if FETCH_FAILED in (sch, gh, hn):
            flags.append("lookup-failed")
        sch = None if sch is FETCH_FAILED else sch
        gh = None if gh is FETCH_FAILED else gh
        hn = None if hn is FETCH_FAILED else hn
    elif not injected:
        flags.append("net:off")

    if not engagement_on:
        gh, hn = None, None

    if sch:
        meta["title"] = sch.get("title")
        if sch.get("is_retracted"):
            return _result(item, 0.0, blocked_name(policy), tier, pat, ["RETRACTED"], meta)

        yr = sch.get("year")
        # `age_years` is only ever set by an injected record (golden tests), so
        # fixtures stay stable as the calendar moves.
        age = (float(sch["age_years"]) if sch.get("age_years") is not None
               else age_years(sch.get("date"), yr))
        c = int(sch.get("citations") or 0)
        cum_pts, vel_pts = citation_points(policy, c, age)
        adj += cum_pts + vel_pts + recency_points(policy, age, field, c)
        notes.append("cit=%s" % human(c))
        if yr:
            notes.append("y%s" % yr)
        meta.update({"citations": c, "year": yr, "venue": sch.get("venue")})

        # Peer review: only a lookup can tell whether a preprint was published.
        # The domain alone never can, which is the whole reason this step exists.
        if peer_review_on:
            pr = policy["peer_review"]
            if is_preprint_host and sch.get("peer_reviewed"):
                adj += float(pr["published_bonus"])
                notes.append("published@%s" % (sch.get("venue") or "venue")[:24])
            elif is_preprint_host:
                adj += float(pr["preprint_penalty"])
                flags.append("preprint")
                uv = pr["unvetted"]
                if age < float(uv["max_age_years"]) and c < int(uv["max_citations"]):
                    adj += float(uv["penalty"])
                    flags.append("unvetted")

        for rule in policy["citation_gap"]:
            if c <= int(rule["max_citations"]) and age > float(rule["min_age_years"]):
                adj += float(rule["penalty"])
                flags.append(rule["flag"])
                break

    elif (use_net and "lookup-failed" not in flags
          and (ids.get("arxiv") or ids.get("doi") or ids.get("pmid"))):
        ni = policy["penalties"]["no_index"]
        grace = ni.get("grace")
        arxiv_id = ids.get("arxiv")
        arxiv_age = None
        if arxiv_id:
            # override lets golden/unit tests pin a deterministic age, same
            # convention as sch["age_years"] above.
            arxiv_age = injected.get("arxiv_age_years")
            if arxiv_age is None:
                arxiv_age = arxiv_id_age_years(arxiv_id)
        if grace and arxiv_age is not None and arxiv_age < float(grace["max_age_years"]):
            adj += float(grace["points"])
            flags.append(grace["flag"])  # too new to be indexed anywhere yet
        else:
            adj += float(ni["points"])
            flags.append(ni["flag"])  # an academic ID in no academic database is suspicious

    if engagement_on:
        ep, enotes = engagement_points(policy, gh, hn)
        adj += ep
        notes.extend(enotes)

    tp_pts, tp_notes = trusted_person_points(policy, url)
    adj += tp_pts
    notes.extend(tp_notes)

    total = max(0.0, min(100.0, base + adj))
    return _result(item, total, verdict_for(policy, total), tier, pat, flags, meta, notes)


def score_many(items, policy, cache, field, use_net, workers=8):
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        rows = list(ex.map(
            lambda it: score_one(it, policy, cache, field, use_net), items))
    return rows
