"""Regex identifier extraction (arXiv, DOI, GitHub, PubMed, OpenReview, bio/medRxiv)."""

from __future__ import annotations

import re
import urllib.parse

from .util import age_years

__all__ = [
    "ARXIV_RE", "DOI_RE", "GITHUB_RE", "PUBMED_RE", "OPENREVIEW_RE", "BIORXIV_RE",
    "GITHUB_NON_REPO", "extract_ids", "arxiv_id_age_years",
]

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


_ARXIV_NEW_ID_RE = re.compile(r"^(\d{2})(\d{2})\.\d{4,5}$")
_ARXIV_OLD_ID_RE = re.compile(r"^[a-z\-]+(?:\.[A-Z]{2})?/(\d{2})(\d{2})\d{3}$", re.I)


def arxiv_id_age_years(arxiv_id: str):
    """Decode the YYMM submission month baked into an arXiv id - both the
    2007+ `YYMM.NNNNN` scheme and the pre-2007 `archive.subj-class/YYMMNNN`
    scheme embed it - and return the paper's age in years with no network
    call. Returns None if `arxiv_id` doesn't match either scheme.
    """
    if not arxiv_id:
        return None
    m = _ARXIV_NEW_ID_RE.match(arxiv_id)
    old = False
    if not m:
        m = _ARXIV_OLD_ID_RE.match(arxiv_id)
        old = True
    if not m:
        return None
    yy, mm = int(m.group(1)), int(m.group(2))
    if not 1 <= mm <= 12:
        return None
    year = 1900 + yy if old and yy >= 91 else 2000 + yy
    return age_years("%04d-%02d-01" % (year, mm), None)
