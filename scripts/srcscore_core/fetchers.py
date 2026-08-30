"""Cache + free external lookups (OpenAlex, Semantic Scholar, GitHub, Hacker News)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from threading import Lock

VERSION = "2.0.0"
UA = "srcscore/%s (research triage)" % VERSION
MAILTO = os.environ.get("SRCSCORE_MAILTO", "").strip()

CACHE_DIR = os.environ.get(
    "SRCSCORE_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "srcscore")
)

__all__ = [
    "VERSION", "UA", "MAILTO", "CACHE_DIR", "Cache", "NullCache", "FETCH_FAILED", "STATS",
    "http_json", "openalex_by_doi", "openalex_by_pmid", "s2_by_arxiv", "arxiv_lookup",
    "github_repo", "hn_points",
]


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
