#!/usr/bin/env python3
"""
srcscore — 출처 신뢰도 채점기 (LLM 토큰 0)

URL 목록을 받아서 1차: 도메인 티어, 2차: 인용수/발행연도/참여지표를 조회해
0~100 점수와 판정(PRIMARY/SUPPORT/SKIM/WEAK/DROP/BLOCKED)을 매깁니다.

표준 라이브러리만 사용합니다. pip install 불필요.

사용법
------
  # 1) URL을 줄단위로 파이프
  cat urls.txt | python3 srcscore.py

  # 2) 인라인
  python3 srcscore.py -u https://arxiv.org/abs/1706.03762 -u https://w3schools.com/x

  # 3) 에이전트가 읽기 좋은 압축 표 (기본)
  python3 srcscore.py --in urls.txt --top 12

  # 4) 통과한 URL만 (다음 단계에서 본문 정독할 대상)
  python3 srcscore.py --in urls.txt --min 60 --format urls

  # 5) 네트워크 없이 도메인 티어만
  python3 srcscore.py --in urls.txt --no-net

입력 형식: 한 줄에 URL 하나. `URL | 제목` 형태도 허용. JSON 배열
([{"url":..,"title":..}]) 도 자동 인식.
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
from datetime import datetime, timezone

VERSION = "1.1.0"
UA = "srcscore/%s (research triage; mailto:opensource@example.com)" % VERSION
MAILTO = os.environ.get("SRCSCORE_MAILTO", "").strip()

HERE = os.path.dirname(os.path.abspath(__file__))
DOMAINS_PATH = os.environ.get("SRCSCORE_DOMAINS", os.path.join(HERE, "domains.json"))
CACHE_DIR = os.environ.get(
    "SRCSCORE_CACHE", os.path.join(os.path.expanduser("~"), ".cache", "srcscore")
)
CACHE_TTL = 14 * 86400  # 14일

TIER_BASE = {1: 88, 2: 74, 3: 60, 4: 46, 5: 32, 6: 14, 0: 0}

# 분야별 인용 반감기(년). 낡음 페널티 계산에 사용.
FIELD_HALFLIFE = {"ai": 3.0, "cs": 4.0, "bio": 6.0, "med": 6.0, "general": 6.0, "policy": 5.0}

VERDICT_BANDS = [
    (78, "PRIMARY"),   # 근거로 직접 인용 가능
    (62, "SUPPORT"),   # 보조 근거로 사용
    (46, "SKIM"),      # 교차확인 후에만
    (30, "WEAK"),      # 배경지식용, 인용 금지
    (0, "DROP"),       # 사용 금지
]


# ----------------------------------------------------------------------------
# 유틸
# ----------------------------------------------------------------------------

def now_year_frac() -> float:
    n = datetime.now(timezone.utc)
    return n.year + (n.timetuple().tm_yday / 365.25)


DEFAULT_DOMAINS = json.loads(r'''
{"tier1": ["nature.com", "science.org", "cell.com", "pnas.org", "nejm.org", "thelancet.com", "bmj.com", "jamanetwork.com", "ieee.org", "ieeexplore.ieee.org", "dl.acm.org", "acm.org", "jmlr.org", "proceedings.mlr.press", "papers.nips.cc", "neurips.cc", "aclanthology.org", "usenix.org", "iso.org", "ietf.org", "rfc-editor.org", "w3.org", "nist.gov", "census.gov", "bls.gov", "federalreserve.gov", "imf.org", "worldbank.org", "oecd.org", "who.int", "cdc.gov", "nih.gov", "pubmed.ncbi.nlm.nih.gov", "eurostat.ec.europa.eu", "kostat.go.kr", "bok.or.kr", "kdi.re.kr", "law.go.kr", "sec.gov", "eur-lex.europa.eu", "uspto.gov", "epo.org", "clinicaltrials.gov", "cochranelibrary.com"], "tier2": ["springer.com", "link.springer.com", "sciencedirect.com", "wiley.com", "onlinelibrary.wiley.com", "tandfonline.com", "sagepub.com", "journals.plos.org", "elifesciences.org", "frontiersin.org", "mdpi.com", "openreview.net", "nber.org", "brookings.edu", "rand.org", "pewresearch.org", "gartner.com", "forrester.com", "idc.com", "mckinsey.com", "bcg.com", "bain.com", "deloitte.com", "pwc.com", "statista.com", "ourworldindata.org", "cmu.edu", "mit.edu", "stanford.edu", "berkeley.edu", "ox.ac.uk", "cam.ac.uk", "ethz.ch", "kaist.ac.kr", "snu.ac.kr", "nasa.gov", "esa.int", "energy.gov", "europa.eu", "go.kr", "korea.kr", "semanticscholar.org", "doi.org", "crossref.org"], "tier3": ["arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com", "osf.io", "papers.ssrn.com", "researchsquare.com", "chemrxiv.org", "anthropic.com", "openai.com", "deepmind.google", "ai.googleblog.com", "research.google", "blog.google", "ai.meta.com", "research.facebook.com", "microsoft.com/en-us/research", "nvidia.com", "developer.nvidia.com", "huggingface.co", "pytorch.org", "tensorflow.org", "jax.readthedocs.io", "docs.python.org", "developer.mozilla.org", "kernel.org", "aws.amazon.com/blogs", "cloud.google.com", "azure.microsoft.com", "docs.claude.com", "platform.openai.com", "docs.anthropic.com", "github.blog", "stripe.com/blog", "netflixtechblog.com", "engineering.fb.com", "eng.uber.com", "slack.engineering", "cloudflare.com", "blog.cloudflare.com", "mozilla.org", "distill.pub", "lilianweng.github.io", "karpathy.github.io", "colah.github.io", "sebastianraschka.com", "epoch.ai", "epochai.org", "lmsys.org", "eleuther.ai", "allenai.org", "bair.berkeley.edu", "thegradient.pub", "jalammar.github.io"], "tier4": ["arstechnica.com", "theverge.com", "wired.com", "technologyreview.com", "spectrum.ieee.org", "quantamagazine.org", "nature.com/news", "ft.com", "economist.com", "wsj.com", "bloomberg.com", "reuters.com", "apnews.com", "nytimes.com", "washingtonpost.com", "theguardian.com", "semianalysis.com", "stratechery.com", "importai.substack.com", "simonwillison.net", "martinfowler.com", "danluu.com", "acoup.blog", "astralcodexten.com", "lesswrong.com", "infoq.com", "thenewstack.io", "theregister.com", "news.ycombinator.com", "github.com"], "tier5": ["medium.com", "towardsdatascience.com", "dev.to", "hashnode.com", "substack.com", "wordpress.com", "blogspot.com", "tistory.com", "velog.io", "brunch.co.kr", "naver.com", "blog.naver.com", "reddit.com", "stackoverflow.com", "stackexchange.com", "quora.com", "x.com", "twitter.com", "linkedin.com", "youtube.com", "wikipedia.org", "cnn.com", "bbc.com", "forbes.com", "businessinsider.com", "techcrunch.com", "venturebeat.com", "zdnet.com", "cnet.com", "engadget.com", "mashable.com", "hankyung.com", "mk.co.kr", "chosun.com", "joongang.co.kr", "etnews.com", "zdnet.co.kr", "bloter.net"], "tier6": ["geeksforgeeks.org", "w3schools.com", "tutorialspoint.com", "javatpoint.com", "simplilearn.com", "guru99.com", "edureka.co", "analyticsvidhya.com", "kdnuggets.com", "datacamp.com/blog", "hackr.io", "codecademy.com/resources", "turing.com/blog", "aimultiple.com", "marketsandmarkets.com", "grandviewresearch.com", "alliedmarketresearch.com", "mordorintelligence.com", "precedenceresearch.com", "fortunebusinessinsights.com", "expertbeacon.com", "byjus.com", "unstop.com", "naukri.com", "linkedin.com/pulse", "quora.com/profile"], "block": ["sci-hub.se", "libgen.is", "researchgate.net/publication/preview", "paperswithcode.com/paper/mirror", "semantic-scholar-mirror.com", "arxiv-sanity-lite.com/mirror", "chatgptdetector.co", "coursehero.com", "scribd.com", "studocu.com", "slideshare.net", "issuu.com", "academia.edu"], "preprint_hosts": ["arxiv.org", "biorxiv.org", "medrxiv.org", "chemrxiv.org", "ssrn.com", "researchsquare.com", "osf.io", "hal.science"], "seo_path_penalties": ["/best-", "/top-10", "/top-5", "/top-7", "/ultimate-guide", "/everything-you-need-to-know", "-in-2023", "-in-2024", "/what-is-", "/complete-guide-to", "/beginners-guide"]}
''')


def _age_years(date_str, year) -> float:
    """publication_date(YYYY-MM-DD)가 있으면 그걸로, 없으면 연도로 나이 계산."""
    if date_str:
        try:
            d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
            return max(0.0, now_year_frac() - (d.year + d.timetuple().tm_yday / 365.25))
        except Exception:
            pass
    if year:
        try:
            return max(0.0, now_year_frac() - float(year))
        except Exception:
            pass
    return 3.0


def load_domains() -> dict:
    """domains.json이 있으면 그걸 쓰고, 없으면 내장 기본 테이블을 쓴다.

    단일 파일로 떼어내 써도 동작하게 하기 위한 폴백이다. 점수표를 손보려면
    domains.json을 스크립트 옆에 두면 그쪽이 우선한다."""
    try:
        with open(DOMAINS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return DEFAULT_DOMAINS


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
    """pattern이 host와 같거나 host의 상위 도메인이면 매치."""
    return host == pattern or host.endswith("." + pattern)


class Cache:
    def __init__(self, path_dir: str):
        self.path = os.path.join(path_dir, "cache.json")
        self.data = {}
        self.dirty = False
        try:
            os.makedirs(path_dir, exist_ok=True)
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except Exception:
            self.data = {}

    def get(self, key):
        e = self.data.get(key)
        if not e:
            return None
        if time.time() - e.get("_t", 0) > CACHE_TTL:
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
        except Exception:
            pass


def http_json(url: str, timeout: float = 12.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


# ----------------------------------------------------------------------------
# 식별자 추출
# ----------------------------------------------------------------------------

ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.I)
GITHUB_RE = re.compile(r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)")
PUBMED_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)")
OPENREVIEW_RE = re.compile(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_\-]+)")
BIORXIV_RE = re.compile(r"(?:bio|med)rxiv\.org/content/(10\.\d{4,9}/[^\s?#]+?)(?:v\d+)?(?:\.full|$|[?#])", re.I)


def extract_ids(url: str) -> dict:
    ids = {}
    m = ARXIV_RE.search(url)
    if m:
        ids["arxiv"] = m.group(1).rstrip("v0123456789") if re.search(r"v\d+$", m.group(1)) else m.group(1)
    m = BIORXIV_RE.search(url)
    if m:
        ids["doi"] = m.group(1)
    if "doi" not in ids:
        m = DOI_RE.search(urllib.parse.unquote(url))
        if m:
            ids["doi"] = m.group(1).rstrip(").,;")
    m = GITHUB_RE.search(url)
    if m and m.group(1).lower() not in ("orgs", "features", "about", "topics", "collections"):
        ids["github"] = (m.group(1), m.group(2).removesuffix(".git"))
    m = PUBMED_RE.search(url)
    if m:
        ids["pmid"] = m.group(1)
    m = OPENREVIEW_RE.search(url)
    if m:
        ids["openreview"] = m.group(1)
    return ids


# ----------------------------------------------------------------------------
# 외부 조회 (전부 무료 · 키 불필요)
# ----------------------------------------------------------------------------

def _oa(path: str) -> str:
    sep = "&" if "?" in path else "?"
    tail = ("%smailto=%s" % (sep, urllib.parse.quote(MAILTO))) if MAILTO else ""
    return "https://api.openalex.org" + path + tail


def openalex_by_doi(doi: str, cache: Cache):
    key = "oa:doi:" + doi.lower()
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    j = http_json(_oa("/works/https://doi.org/" + urllib.parse.quote(doi, safe="/")))
    out = _parse_openalex(j) if j else None
    cache.put(key, out or {})
    return out


def openalex_by_pmid(pmid: str, cache: Cache):
    key = "oa:pmid:" + pmid
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    j = http_json(_oa("/works/pmid:" + pmid))
    out = _parse_openalex(j) if j else None
    cache.put(key, out or {})
    return out


def _parse_openalex(j: dict):
    if not isinstance(j, dict) or "id" not in j:
        return None
    prim = (j.get("primary_location") or {}).get("source") or {}
    venue_names, venue_types = [], []
    for loc in (j.get("locations") or []):
        s = loc.get("source") or {}
        if s.get("display_name"):
            venue_names.append(s["display_name"])
            venue_types.append((s.get("type") or "").lower())
    peer_reviewed = any(t in ("journal", "conference", "book series") for t in venue_types) and \
        not all(("repository" in t or not t) for t in venue_types)
    return {
        "src": "openalex",
        "title": j.get("display_name"),
        "citations": j.get("cited_by_count") or 0,
        "year": j.get("publication_year"),
        "date": j.get("publication_date"),
        "is_retracted": bool(j.get("is_retracted")),
        "is_preprint": (j.get("type") or "") == "preprint",
        "venue": prim.get("display_name"),
        "venue_type": (prim.get("type") or "").lower(),
        "peer_reviewed": bool(peer_reviewed),
        "refs": j.get("referenced_works_count") or 0,
        "oa": bool((j.get("open_access") or {}).get("is_oa")),
    }


def s2_by_arxiv(arxiv_id: str, cache: Cache):
    key = "s2:arxiv:" + arxiv_id
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    fields = "title,citationCount,influentialCitationCount,year,publicationDate,venue,publicationVenue,externalIds,isOpenAccess"
    j = http_json(
        "https://api.semanticscholar.org/graph/v1/paper/arXiv:%s?fields=%s"
        % (urllib.parse.quote(arxiv_id), fields)
    )
    out = None
    if isinstance(j, dict) and j.get("paperId"):
        pv = j.get("publicationVenue") or {}
        vtype = (pv.get("type") or "").lower()
        venue = j.get("venue") or pv.get("name")
        peer = bool(venue) and "arxiv" not in str(venue).lower()
        out = {
            "src": "s2",
            "title": j.get("title"),
            "citations": j.get("citationCount") or 0,
            "influential": j.get("influentialCitationCount") or 0,
            "year": j.get("year"),
            "date": j.get("publicationDate"),
            "is_retracted": False,
            "is_preprint": not peer,
            "venue": venue,
            "venue_type": vtype,
            "peer_reviewed": peer,
            "refs": 0,
            "oa": bool(j.get("isOpenAccess")),
        }
    cache.put(key, out or {})
    return out


def arxiv_lookup(arxiv_id: str, cache: Cache):
    """OpenAlex(10.48550 DOI) 우선, 실패 시 Semantic Scholar."""
    r = openalex_by_doi("10.48550/arXiv." + arxiv_id, cache)
    if r and (r.get("citations") or r.get("year")):
        return r
    return s2_by_arxiv(arxiv_id, cache) or r


def github_stars(owner: str, repo: str, cache: Cache):
    key = "gh:%s/%s" % (owner.lower(), repo.lower())
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    j = http_json("https://api.github.com/repos/%s/%s" % (urllib.parse.quote(owner), urllib.parse.quote(repo)))
    out = None
    if isinstance(j, dict) and "stargazers_count" in j:
        out = {
            "stars": j.get("stargazers_count") or 0,
            "forks": j.get("forks_count") or 0,
            "archived": bool(j.get("archived")),
            "pushed_at": j.get("pushed_at"),
        }
    cache.put(key, out or {})
    return out


def hn_points(url: str, cache: Cache):
    key = "hn:" + url
    hit = cache.get(key)
    if hit is not None:
        return hit or None
    q = urllib.parse.quote(url, safe="")
    j = http_json(
        "https://hn.algolia.com/api/v1/search?query=%s&restrictSearchableAttributes=url&hitsPerPage=5" % q
    )
    out = None
    if isinstance(j, dict) and j.get("hits"):
        best = max(j["hits"], key=lambda h: (h.get("points") or 0))
        if (best.get("points") or 0) > 0:
            out = {"points": best.get("points") or 0, "comments": best.get("num_comments") or 0}
    cache.put(key, out or {})
    return out


# ----------------------------------------------------------------------------
# 채점
# ----------------------------------------------------------------------------

def match_tier(url: str, tables: dict):
    hp = host_path(url)
    host = norm_host(url)
    best = None  # (specificity, tier, pattern)
    for tier_name, tier_num in (
        ("block", 0), ("tier6", 6), ("tier5", 5), ("tier4", 4),
        ("tier3", 3), ("tier2", 2), ("tier1", 1),
    ):
        for pat in tables.get(tier_name, []):
            p = pat.lower()
            if "/" in p:
                if hp.lower().startswith(p) or ("/" + p) in ("/" + hp.lower()):
                    cand = (len(p) + 100, tier_num, pat)
                    if best is None or cand[0] > best[0]:
                        best = cand
            elif host_matches(host, p):
                cand = (len(p), tier_num, pat)
                if best is None or cand[0] > best[0]:
                    best = cand
    if best is None:
        return 5, None  # 미등록 도메인 = 일반 웹, tier5로 시작
    return best[1], best[2]


def cite_points(citations: int, age_years: float) -> tuple:
    """(누적 인용 점수, 인용 속도 점수)"""
    c = max(0, int(citations or 0))
    # 속도 계산의 나이 하한을 0.75년으로 둔다. 하한이 너무 작으면 갓 나온
    # 논문의 인용 속도가 폭발해 검증되지 않은 프리프린트가 과대평가된다.
    age = max(0.75, age_years)
    base = min(20.0, 6.2 * math.log10(1 + c))
    vel = c / age
    vpts = min(9.0, 4.2 * math.log10(1 + vel))
    return base, vpts


def recency_points(age_years: float, field: str, citations: int) -> float:
    hl = FIELD_HALFLIFE.get(field, 6.0)
    a = max(0.0, age_years)
    if a < 1.0:
        return 4.0
    if a < 2.0:
        return 2.0
    # 반감기 기준 지수 감쇠 → 최대 -12
    decay = -12.0 * (1 - 0.5 ** ((a - 2.0) / hl))
    # 고인용 고전은 낡음 페널티 면제
    if citations >= 1000:
        decay = max(decay, 0.0)
    elif citations >= 300:
        decay *= 0.4
    return decay


def engagement_points(gh, hn) -> tuple:
    pts, notes = 0.0, []
    if gh:
        s = gh.get("stars", 0)
        pts += min(14.0, 3.6 * math.log10(1 + s))
        notes.append("★%s" % _human(s))
        if gh.get("archived"):
            pts -= 4.0
            notes.append("archived")
    if hn:
        p = hn.get("points", 0)
        pts += min(8.0, 3.0 * math.log10(1 + p))
        notes.append("HN%d" % p)
    return pts, notes


def _human(n) -> str:
    n = int(n or 0)
    if n >= 1000000:
        return "%.1fM" % (n / 1e6)
    if n >= 1000:
        return "%.1fk" % (n / 1000)
    return str(n)


def verdict_for(score: float) -> str:
    for thr, name in VERDICT_BANDS:
        if score >= thr:
            return name
    return "DROP"


def score_one(item: dict, tables: dict, cache: Cache, field: str, use_net: bool) -> dict:
    url = item["url"]
    tier, pat = match_tier(url, tables)
    base = TIER_BASE[tier]
    notes = []
    flags = []
    adj = 0.0
    meta = {}

    host = norm_host(url)
    is_preprint_host = any(host_matches(host, p) for p in tables.get("preprint_hosts", []))

    if tier == 0:
        return _result(item, 0.0, "BLOCKED", tier, pat, ["blocklist"], meta)

    # SEO 경로 페널티
    low = url.lower()
    for frag in tables.get("seo_path_penalties", []):
        if frag in low:
            adj -= 6.0
            flags.append("seo-path")
            break

    ids = extract_ids(url)
    sch = None
    gh = hn = None

    if use_net:
        if "arxiv" in ids:
            sch = arxiv_lookup(ids["arxiv"], cache)
        elif "doi" in ids:
            sch = openalex_by_doi(ids["doi"], cache)
        elif "pmid" in ids:
            sch = openalex_by_pmid(ids["pmid"], cache)
        if "github" in ids:
            gh = github_stars(ids["github"][0], ids["github"][1], cache)
        if sch is None and gh is None and tier >= 3:
            hn = hn_points(url, cache)
    else:
        flags.append("net:off")

    if sch:
        meta["title"] = sch.get("title")
        if sch.get("is_retracted"):
            return _result(item, 0.0, "BLOCKED", tier, pat, ["RETRACTED"], meta)
        yr = sch.get("year")
        age = _age_years(sch.get("date"), yr)
        c = sch.get("citations") or 0
        cp, vp = cite_points(c, age)
        rp = recency_points(age, field, c)
        adj += cp + vp + rp
        notes.append("cit=%s" % _human(c))
        if yr:
            notes.append("y%s" % yr)
        meta.update({"citations": c, "year": yr, "venue": sch.get("venue")})

        # 프리프린트가 정식 게재된 경우 승격, 아직 프리프린트면 감점.
        # 동료심사를 통과했는지 여부는 도메인만으로는 알 수 없고, 이 조회가
        # 그걸 알아내는 유일한 지점이다.
        if is_preprint_host and sch.get("peer_reviewed"):
            adj += 9.0
            notes.append("published@%s" % (sch.get("venue") or "venue")[:24])
        elif is_preprint_host:
            adj -= 8.0
            flags.append("preprint")
            # 나온 지 얼마 안 됐고 아무도 인용하지 않은 프리프린트 =
            # 아직 커뮤니티 검증이 전혀 없는 상태
            if age < 1.0 and c < 5:
                adj -= 4.0
                flags.append("unvetted")
        # 나이에 비해 인용이 없으면 감점
        if c == 0 and age > 2.0:
            adj -= 6.0
            flags.append("uncited")
        elif c < 10 and age > 4.0:
            adj -= 6.0
            flags.append("low-cite")
    elif use_net and (ids.get("arxiv") or ids.get("doi") or ids.get("pmid")):
        flags.append("no-index")   # 학술 DB에 없음 = 의심
        adj -= 5.0

    ep, enotes = engagement_points(gh, hn)
    adj += ep
    notes.extend(enotes)

    total = max(0.0, min(100.0, base + adj))
    return _result(item, total, verdict_for(total), tier, pat, flags, meta, notes)


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


# ----------------------------------------------------------------------------
# 입출력
# ----------------------------------------------------------------------------

def parse_input(text: str) -> list:
    text = text.strip()
    if not text:
        return []
    if text.startswith("[") or text.startswith("{"):
        try:
            j = json.loads(text)
            if isinstance(j, dict):
                j = j.get("results") or j.get("urls") or []
            out = []
            for e in j:
                if isinstance(e, str):
                    out.append({"url": e})
                elif isinstance(e, dict) and e.get("url"):
                    out.append({"url": e["url"], "title": e.get("title", "")})
            return out
        except Exception:
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


def render_table(rows: list) -> str:
    if not rows:
        return "(no sources)"
    lines = ["SCORE VERDICT T  SIGNALS                     URL"]
    for r in rows:
        sig = ",".join(r["signals"] + r["flags"])[:27]
        lines.append(
            "%5.1f %-8s %-2s %-27s %s"
            % (r["score"], r["verdict"], r["tier"], sig, r["url"])
        )
    keep = sum(1 for r in rows if r["verdict"] in ("PRIMARY", "SUPPORT"))
    lines.append("-- %d sources | %d citable | %d dropped"
                 % (len(rows), keep,
                    sum(1 for r in rows if r["verdict"] in ("DROP", "BLOCKED"))))
    return "\n".join(lines)


def render_md(rows: list) -> str:
    out = ["| 점수 | 판정 | T | 근거 | 출처 |", "|---|---|---|---|---|"]
    for r in rows:
        t = (r["title"] or r["url"])[:70]
        sig = ", ".join(r["signals"] + r["flags"]) or "-"
        out.append("| %.1f | %s | %d | %s | [%s](%s) |" % (r["score"], r["verdict"], r["tier"], sig, t.replace("|", "/"), r["url"]))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="출처 신뢰도 채점기 (토큰 0)")
    ap.add_argument("--in", dest="infile", default="-", help="입력 파일 (기본: stdin)")
    ap.add_argument("-u", "--url", action="append", default=[], help="URL 직접 지정 (반복 가능)")
    ap.add_argument("--format", choices=["table", "json", "md", "urls"], default="table")
    ap.add_argument("--min", type=float, default=None, help="이 점수 미만 제외")
    ap.add_argument("--top", type=int, default=None, help="상위 N개만 출력")
    ap.add_argument("--field", default="ai", choices=sorted(FIELD_HALFLIFE), help="분야 (인용 반감기)")
    ap.add_argument("--no-net", action="store_true", help="외부 조회 없이 도메인 티어만")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--version", action="version", version=VERSION)
    a = ap.parse_args(argv)

    items = [{"url": u} for u in a.url]
    if not items:
        raw = sys.stdin.read() if a.infile == "-" else open(a.infile, encoding="utf-8").read()
        items = parse_input(raw)
    if not items:
        print("입력된 URL이 없습니다.", file=sys.stderr)
        return 2

    tables = load_domains()
    cache = Cache(CACHE_DIR)
    use_net = not a.no_net

    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        rows = list(ex.map(lambda it: score_one(it, tables, cache, a.field, use_net), items))
    cache.flush()

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
