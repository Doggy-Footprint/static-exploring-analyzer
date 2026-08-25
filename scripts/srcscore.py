#!/usr/bin/env python3
"""
srcscore - deterministic source credibility scorer (zero LLM tokens).

Takes a list of URLs, applies the domain tier as a base score, then adjusts it
with citation counts, publication date, peer-review status and engagement
signals fetched from free APIs. Emits 0-100 scores and a verdict
(PRIMARY / SUPPORT / SKIM / WEAK / DROP / BLOCKED).

Every threshold, coefficient and domain list lives in scripts/policy.json.
Nothing about scoring is hard-coded here - edit the policy file, not this file.

Standard library only. No pip install.

USAGE
-----
  # pipe URLs, one per line
  cat urls.txt | python3 srcscore.py

  # inline
  python3 srcscore.py -u https://arxiv.org/abs/1706.03762

  # compact table for an agent to read (default)
  python3 srcscore.py --in urls.txt --top 12

  # only what passed, for the reading step
  python3 srcscore.py --in urls.txt --min 62 --format urls

  # domain tier only, no network
  python3 srcscore.py --in urls.txt --no-net

Input: one URL per line. `URL | title` is accepted. A JSON array
([{"url": ..., "title": ...}] or ["https://..."]) is auto-detected.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from datetime import datetime, timezone

VERSION = "2.0.0"
UA = "srcscore/%s (research triage)" % VERSION
MAILTO = os.environ.get("SRCSCORE_MAILTO", "").strip()

HERE = os.path.dirname(os.path.abspath(__file__))
POLICY_PATH = os.environ.get("SRCSCORE_POLICY", os.path.join(HERE, "policy.json"))
CACHE_DIR = os.environ.get(
    "SRCSCORE_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "srcscore")
)


class PolicyError(RuntimeError):
    """policy.json is missing, unreadable or structurally invalid."""


# ----------------------------------------------------------------------------
# Policy
# ----------------------------------------------------------------------------

REQUIRED_KEYS = (
    "defaults", "tiers", "verdicts", "field_halflife_years", "citations",
    "recency", "peer_review", "citation_gap", "engagement", "penalties",
    "seo_path_patterns", "preprint_hosts", "domains",
)


def load_policy(path: str = None) -> dict:
    """Read policy.json. Raises PolicyError - there is no silent fallback:
    scoring with a guessed policy would be worse than not scoring at all."""
    path = path or POLICY_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            p = json.load(f)
    except OSError as e:
        raise PolicyError("cannot read policy file %s: %s" % (path, e))
    except ValueError as e:
        raise PolicyError("policy file %s is not valid JSON: %s" % (path, e))
    validate_policy(p, path)
    return p


def validate_policy(p: dict, path: str = "policy") -> None:
    """Structural sanity check. Cheap, and it turns a silent mis-score into a
    loud failure at load time."""
    def bad(msg):
        raise PolicyError("%s: %s" % (path, msg))

    if not isinstance(p, dict):
        bad("top level must be an object")
    for k in REQUIRED_KEYS:
        if k not in p:
            bad("missing required key %r" % k)

    tiers = p["tiers"]
    for name, spec in tiers.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("base"), (int, float)):
            bad("tiers.%s needs a numeric 'base'" % name)
    if "block" not in tiers:
        bad("tiers must define 'block'")

    bands = (p["verdicts"] or {}).get("bands") or []
    if not bands:
        bad("verdicts.bands is empty")
    mins = [b.get("min") for b in bands]
    if any(not isinstance(m, (int, float)) for m in mins):
        bad("every verdict band needs a numeric 'min'")
    if mins != sorted(mins, reverse=True):
        bad("verdicts.bands must be ordered from highest 'min' to lowest")
    if mins[-1] != 0:
        bad("the lowest verdict band must start at 0")

    dom = p["domains"]
    seen = {}
    for tier_name, patterns in dom.items():
        if tier_name not in tiers:
            bad("domains.%s has no matching tier definition" % tier_name)
        if not isinstance(patterns, list):
            bad("domains.%s must be a list" % tier_name)
        for pat in patterns:
            key = str(pat).lower()
            if key in seen:
                bad("domain %r listed in both %s and %s" % (pat, seen[key], tier_name))
            seen[key] = tier_name

    default_tier = str((p["defaults"] or {}).get("unregistered_tier", ""))
    if default_tier not in tiers:
        bad("defaults.unregistered_tier %r has no tier definition" % default_tier)
    if (p["defaults"] or {}).get("field") not in p["field_halflife_years"]:
        bad("defaults.field is not present in field_halflife_years")


def tier_base(policy: dict, tier: str) -> float:
    return float(policy["tiers"][tier]["base"])


def verdict_for(policy: dict, score: float) -> str:
    for band in policy["verdicts"]["bands"]:
        if score >= band["min"]:
            return band["name"]
    return policy["verdicts"]["bands"][-1]["name"]


def blocked_name(policy: dict) -> str:
    return policy["verdicts"].get("blocked_name", "BLOCKED")


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

def now_year_frac() -> float:
    n = datetime.now(timezone.utc)
    return n.year + (n.timetuple().tm_yday / 365.25)


def age_years(date_str, year) -> float:
    """Prefer a full publication_date; fall back to the year; else assume 3y."""
    if date_str:
        try:
            d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
            return max(0.0, now_year_frac() - (d.year + d.timetuple().tm_yday / 365.25))
        except ValueError:
            pass
    if year:
        try:
            return max(0.0, now_year_frac() - float(year))
        except (TypeError, ValueError):
            pass
    return 3.0


def norm_host(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url if "//" in url else "https://" + url)
    except ValueError:
        return ""
    h = (p.netloc or "").lower()
    if "@" in h:
        h = h.split("@", 1)[1]
    h = h.split(":", 1)[0]
    return h[4:] if h.startswith("www.") else h


def host_path(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url if "//" in url else "https://" + url)
    except ValueError:
        return ""
    return norm_host(url) + (p.path or "")


def host_matches(host: str, pattern: str) -> bool:
    """A pattern matches a host itself or any of its subdomains."""
    return host == pattern or host.endswith("." + pattern)


def human(n) -> str:
    n = int(n or 0)
    if n >= 1000000:
        return "%.1fM" % (n / 1e6)
    if n >= 1000:
        return "%.1fk" % (n / 1000)
    return str(n)


# ----------------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------------

class Cache:
    def __init__(self, path_dir: str, ttl_days: float = 14.0):
        self.path = os.path.join(path_dir, "cache.json")
        self.ttl = ttl_days * 86400
        self.data = {}
        self.dirty = False
        try:
            os.makedirs(path_dir, exist_ok=True)
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def get(self, key):
        e = self.data.get(key)
        if not e or time.time() - e.get("_t", 0) > self.ttl:
            return None
        return e.get("v")

    def put(self, key, value):
        self.data[key] = {"_t": time.time(), "v": value}
        self.dirty = True

    def flush(self):
        if not self.dirty:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass


class NullCache(Cache):
    def __init__(self):  # noqa: D107 - used by golden tests
        self.data, self.dirty, self.ttl, self.path = {}, False, 0, ""

    def get(self, key):
        return None

    def put(self, key, value):
        pass

    def flush(self):
        pass


FETCH_FAILED = object()   # transport error: "we could not ask", not "no such record"

STATS = {"lookups": 0, "failed": 0}
_STATS_LOCK = Lock()


def _count(failed: bool):
    with _STATS_LOCK:
        STATS["lookups"] += 1
        if failed:
            STATS["failed"] += 1


def http_json(url: str, timeout: float = 12.0):
    """Returns parsed JSON, None when the service answered but had nothing, or
    FETCH_FAILED when we could not reach it at all. The distinction matters:
    an unreachable API must never be scored as `no-index`."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            _count(False)
            if r.status != 200:
                return None
            return json.loads(body)
    except urllib.error.HTTPError as e:
        # 404/410 = genuinely absent. 401/403/407/429/5xx = we were refused or
        # throttled, which is a reachability problem, not evidence of absence.
        if e.code in (401, 403, 407, 429) or e.code >= 500:
            _count(True)
            return FETCH_FAILED
        _count(False)
        return None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        _count(True)
        return FETCH_FAILED


# ----------------------------------------------------------------------------
# Identifier extraction
# ----------------------------------------------------------------------------

ARXIV_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.I)
GITHUB_RE = re.compile(r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)")
PUBMED_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)")
OPENREVIEW_RE = re.compile(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_\-]+)")
BIORXIV_RE = re.compile(
    r"(?:bio|med)rxiv\.org/content/(10\.\d{4,9}/[^\s?#]+?)(?:v\d+)?(?:\.full|$|[?#])", re.I)

GITHUB_NON_REPO = ("orgs", "features", "about", "topics", "collections", "sponsors")


def extract_ids(url: str) -> dict:
    ids = {}
    m = ARXIV_RE.search(url)
    if m:
        raw = m.group(1)
        ids["arxiv"] = re.sub(r"v\d+$", "", raw)
    m = BIORXIV_RE.search(url)
    if m:
        ids["doi"] = m.group(1)
    if "doi" not in ids:
        m = DOI_RE.search(urllib.parse.unquote(url))
        if m:
            ids["doi"] = m.group(1).rstrip(").,;")
    m = GITHUB_RE.search(url)
    if m and m.group(1).lower() not in GITHUB_NON_REPO:
        ids["github"] = (m.group(1), m.group(2).removesuffix(".git"))
    m = PUBMED_RE.search(url)
    if m:
        ids["pmid"] = m.group(1)
    m = OPENREVIEW_RE.search(url)
    if m:
        ids["openreview"] = m.group(1)
    return ids


# ----------------------------------------------------------------------------
# External lookups (all free, no API key)
# ----------------------------------------------------------------------------

def _oa(path: str) -> str:
    sep = "&" if "?" in path else "?"
    tail = ("%smailto=%s" % (sep, urllib.parse.quote(MAILTO))) if MAILTO else ""
    return "https://api.openalex.org" + path + tail


def _parse_openalex(j):
    if not isinstance(j, dict) or "id" not in j:
        return None
    prim = (j.get("primary_location") or {}).get("source") or {}
    venue_types = []
    for loc in (j.get("locations") or []):
        s = loc.get("source") or {}
        if s.get("display_name"):
            venue_types.append((s.get("type") or "").lower())
    peer_reviewed = (
        any(t in ("journal", "conference", "book series") for t in venue_types)
        and not all(("repository" in t or not t) for t in venue_types)
    )
    return {
        "src": "openalex",
        "title": j.get("display_name"),
        "citations": j.get("cited_by_count") or 0,
        "year": j.get("publication_year"),
        "date": j.get("publication_date"),
        "is_retracted": bool(j.get("is_retracted")),
        "venue": prim.get("display_name"),
        "peer_reviewed": bool(peer_reviewed),
    }


def openalex_by_doi(doi: str, cache: Cache, timeout: float):
    key = "oa:doi:" + doi.lower()
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    j = http_json(_oa("/works/https://doi.org/" + urllib.parse.quote(doi, safe="/")), timeout)
    if j is FETCH_FAILED:
        return FETCH_FAILED
    out = _parse_openalex(j) if j else None
    cache.put(key, out or {})
    return out


def openalex_by_pmid(pmid: str, cache: Cache, timeout: float):
    key = "oa:pmid:" + pmid
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    j = http_json(_oa("/works/pmid:" + pmid), timeout)
    if j is FETCH_FAILED:
        return FETCH_FAILED
    out = _parse_openalex(j) if j else None
    cache.put(key, out or {})
    return out


def s2_by_arxiv(arxiv_id: str, cache: Cache, timeout: float):
    key = "s2:arxiv:" + arxiv_id
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    fields = "title,citationCount,year,publicationDate,venue,publicationVenue,isOpenAccess"
    j = http_json(
        "https://api.semanticscholar.org/graph/v1/paper/arXiv:%s?fields=%s"
        % (urllib.parse.quote(arxiv_id), fields), timeout)
    if j is FETCH_FAILED:
        return FETCH_FAILED
    out = None
    if isinstance(j, dict) and j.get("paperId"):
        pv = j.get("publicationVenue") or {}
        venue = j.get("venue") or pv.get("name")
        peer = bool(venue) and "arxiv" not in str(venue).lower()
        out = {
            "src": "s2",
            "title": j.get("title"),
            "citations": j.get("citationCount") or 0,
            "year": j.get("year"),
            "date": j.get("publicationDate"),
            "is_retracted": False,
            "venue": venue,
            "peer_reviewed": peer,
        }
    cache.put(key, out or {})
    return out


def arxiv_lookup(arxiv_id: str, cache: Cache, timeout: float):
    """OpenAlex via the 10.48550 DOI first, Semantic Scholar as fallback."""
    r = openalex_by_doi("10.48550/arXiv." + arxiv_id, cache, timeout)
    if r is not FETCH_FAILED and r and (r.get("citations") or r.get("year")):
        return r
    s2 = s2_by_arxiv(arxiv_id, cache, timeout)
    if s2 is FETCH_FAILED:
        return r if (r and r is not FETCH_FAILED) else FETCH_FAILED
    return s2 or (None if r is FETCH_FAILED else r)


def github_repo(owner: str, repo: str, cache: Cache, timeout: float):
    key = "gh:%s/%s" % (owner.lower(), repo.lower())
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    j = http_json("https://api.github.com/repos/%s/%s"
                  % (urllib.parse.quote(owner), urllib.parse.quote(repo)), timeout)
    if j is FETCH_FAILED:
        return FETCH_FAILED
    out = None
    if isinstance(j, dict) and "stargazers_count" in j:
        out = {
            "stars": j.get("stargazers_count") or 0,
            "forks": j.get("forks_count") or 0,
            "archived": bool(j.get("archived")),
        }
    cache.put(key, out or {})
    return out


def hn_points(url: str, cache: Cache, timeout: float):
    key = "hn:" + url
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    q = urllib.parse.quote(url, safe="")
    j = http_json(
        "https://hn.algolia.com/api/v1/search?query=%s"
        "&restrictSearchableAttributes=url&hitsPerPage=5" % q, timeout)
    if j is FETCH_FAILED:
        return FETCH_FAILED
    out = None
    if isinstance(j, dict) and j.get("hits"):
        best = max(j["hits"], key=lambda h: (h.get("points") or 0))
        if (best.get("points") or 0) > 0:
            out = {"points": best.get("points") or 0, "comments": best.get("num_comments") or 0}
    cache.put(key, out or {})
    return out


# ----------------------------------------------------------------------------
# Scoring - every number below comes out of policy.json
# ----------------------------------------------------------------------------

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


def score_one(item: dict, policy: dict, cache: Cache, field: str,
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
        if gh is None and "github" in ids:
            gh = github_repo(ids["github"][0], ids["github"][1], cache, timeout)
        min_tier = int(policy["engagement"]["hackernews"]["min_tier"])
        if hn is None and sch is None and gh is None and tier.isdigit() and int(tier) >= min_tier:
            hn = hn_points(url, cache, timeout)
        if FETCH_FAILED in (sch, gh, hn):
            flags.append("lookup-failed")
        sch = None if sch is FETCH_FAILED else sch
        gh = None if gh is FETCH_FAILED else gh
        hn = None if hn is FETCH_FAILED else hn
    elif not injected:
        flags.append("net:off")

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
        adj += float(ni["points"])
        flags.append(ni["flag"])  # an academic ID in no academic database is suspicious

    ep, enotes = engagement_points(policy, gh, hn)
    adj += ep
    notes.extend(enotes)

    total = max(0.0, min(100.0, base + adj))
    return _result(item, total, verdict_for(policy, total), tier, pat, flags, meta, notes)


# ----------------------------------------------------------------------------
# Input / output
# ----------------------------------------------------------------------------

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


def score_many(items, policy, cache, field, use_net, workers=8):
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        rows = list(ex.map(
            lambda it: score_one(it, policy, cache, field, use_net), items))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic source credibility scorer (zero LLM tokens).")
    ap.add_argument("--in", dest="infile", default="-", help="input file (default: stdin)")
    ap.add_argument("-u", "--url", action="append", default=[],
                    help="score this URL (repeatable)")
    ap.add_argument("--format", choices=["table", "json", "md", "urls"], default="table")
    ap.add_argument("--min", type=float, default=None, help="drop anything below this score")
    ap.add_argument("--top", type=int, default=None, help="keep only the top N")
    ap.add_argument("--field", default=None,
                    help="research field, sets the citation half-life (see policy.json)")
    ap.add_argument("--no-net", action="store_true",
                    help="domain tier only, no external lookups")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--policy", default=None, help="path to policy.json")
    ap.add_argument("--version", action="version", version=VERSION)
    a = ap.parse_args(argv)

    try:
        policy = load_policy(a.policy)
    except PolicyError as e:
        print("srcscore: %s" % e, file=sys.stderr)
        return 3

    field = a.field or policy["defaults"]["field"]
    if field not in policy["field_halflife_years"]:
        print("srcscore: unknown --field %r (known: %s)"
              % (field, ", ".join(sorted(policy["field_halflife_years"]))), file=sys.stderr)
        return 2

    items = [{"url": u, "title": ""} for u in a.url]
    if not items:
        try:
            raw = sys.stdin.read() if a.infile == "-" else open(a.infile, encoding="utf-8").read()
        except OSError as e:
            print("srcscore: cannot read input: %s" % e, file=sys.stderr)
            return 2
        items = parse_input(raw)
    if not items:
        print("srcscore: no URLs in input", file=sys.stderr)
        return 2

    cache = Cache(CACHE_DIR, float(policy["defaults"].get("cache_ttl_days", 14)))
    workers = a.workers or int(policy["defaults"].get("workers", 8))
    rows = score_many(items, policy, cache, field, not a.no_net, workers)
    cache.flush()

    # A blocked or offline API would otherwise degrade every academic source to
    # its bare domain tier without saying so. Fail loudly instead.
    if STATS["failed"]:
        msg = ("srcscore: %d of %d external lookups could not be reached"
               % (STATS["failed"], STATS["lookups"]))
        if STATS["failed"] * 2 >= STATS["lookups"]:
            print("%s.\nScores would be domain-tier only and misleading. Check network "
                  "access to api.openalex.org / api.semanticscholar.org, or re-run with "
                  "--no-net to accept domain-only scoring deliberately." % msg, file=sys.stderr)
            return 4
        print("%s; those rows are flagged `lookup-failed`." % msg, file=sys.stderr)

    rows.sort(key=lambda r: -r["score"])
    if a.min is not None:
        rows = [r for r in rows if r["score"] >= a.min]
    if a.top:
        rows = rows[: a.top]

    if a.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    elif a.format == "md":
        print(render_md(rows))
    elif a.format == "urls":
        for r in rows:
            print(r["url"])
    else:
        print(render_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
