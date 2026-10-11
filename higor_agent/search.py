"""Literature sources: Semantic Scholar (keyword, venue, citations, recommendations)
and the arXiv listing API. Plain async functions returning ``Paper`` lists.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

from higor_agent.models import Paper, SearchError

logger = logging.getLogger(__name__)

S2_API = "https://api.semanticscholar.org/graph/v1"
S2_RECOMMENDATIONS_API = "https://api.semanticscholar.org/recommendations/v1/papers"
ARXIV_API = "https://export.arxiv.org/api/query"
CROSSREF_API = "https://api.crossref.org/works"
_USER_AGENT = "higor-agent/0.1 (https://github.com/gabrielepenazzi/higor-agent)"

_PAPER_FIELDS = (
    "paperId,title,authors,abstract,externalIds,publicationVenue,venue,"
    "publicationDate,year,citationCount"
)
_S2_RETRY_DELAYS = (3.0, 8.0, 20.0, 45.0)
S2_MIN_INTERVAL = 1.1  # seconds between unauthenticated S2 requests (free tier ≈ 1 req/s)
_ARXIV_MIN_INTERVAL = 3.0
_last_call: dict[str, float] = {"s2": 0.0, "arxiv": 0.0}
_locks: dict[tuple[str, int], asyncio.Lock] = {}


def _lock(name: str) -> asyncio.Lock:
    key = (name, id(asyncio.get_running_loop()))
    return _locks.setdefault(key, asyncio.Lock())


async def _throttle(name: str, interval: float) -> None:
    wait = interval - (time.monotonic() - _last_call[name])
    if wait > 0:
        await asyncio.sleep(wait)
    _last_call[name] = time.monotonic()


def _s2_headers() -> dict[str, str]:
    key = os.environ.get("S2_API_KEY", "")
    return {"x-api-key": key} if key else {}


async def _s2_request(method: str, url: str, *, params: dict | None = None, body: dict | None = None):
    """One S2 call, serialised and throttled, with retry on 429."""
    interval = 0.1 if _s2_headers() else S2_MIN_INTERVAL
    async with _lock("s2"):
        try:
            async with httpx.AsyncClient(timeout=40, headers=_s2_headers()) as client:
                for delay in (None, *_S2_RETRY_DELAYS):
                    if delay is not None:
                        logger.debug("S2 rate-limited; retrying in %.0fs", delay)
                        await asyncio.sleep(delay)
                    await _throttle("s2", interval)
                    resp = await client.request(method, url, params=params, json=body)
                    if resp.status_code == 429:
                        continue
                    resp.raise_for_status()
                    return resp.json()
        except httpx.HTTPError as exc:
            raise SearchError(f"Semantic Scholar request failed: {exc}") from exc
    raise SearchError(f"Semantic Scholar rate limit persists after retries: {url}")


def _date_range(days: int) -> str:
    end = datetime.now(tz=timezone.utc).date()
    start = end - timedelta(days=days)
    return f"{start.isoformat()}:{end.isoformat()}"


def _cutoff(days: int | None) -> datetime | None:
    if days is None:
        return None
    return datetime.now(tz=timezone.utc) - timedelta(days=days)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def within(paper: Paper, cutoff: datetime | None) -> bool:
    """True if the paper is undated or published on/after *cutoff* (None = no limit)."""
    if cutoff is None or paper.published is None:
        return True
    return _as_utc(paper.published) >= cutoff


def phrase_query(phrases: list[str]) -> str:
    """Build an S2 bulk query matching any of the exact phrases: ``"a" | "b"``."""
    cleaned = [p.replace('"', "").strip() for p in phrases]
    return " | ".join(f'"{p}"' for p in cleaned if p)


# ── Semantic Scholar ──────────────────────────────────────────────────────────

async def s2_search(
    query: str,
    *,
    days: int,
    fields_of_study: list[str] | None = None,
    venues: list[str] | None = None,
    limit: int = 300,
) -> list[Paper]:
    """Bulk search with a server-side date window, optional field and venue filters."""
    params: dict = {
        "query": query,
        "publicationDateOrYear": _date_range(days),
        "fields": _PAPER_FIELDS,
        "sort": "publicationDate:desc",
    }
    if fields_of_study:
        params["fieldsOfStudy"] = ",".join(fields_of_study)
    if venues:
        params["venue"] = ",".join(venues)

    papers: list[Paper] = []
    token: str | None = None
    while True:
        if token:
            params["token"] = token
        data = await _s2_request("GET", f"{S2_API}/paper/search/bulk", params=params)
        papers.extend(_parse_items(data.get("data", [])))
        token = data.get("token")
        if not token or len(papers) >= limit:
            break
    return papers[:limit]


async def s2_citations_of(
    pairs: list[tuple[str, str]], *, days: int | None, per_paper: int = 100
) -> list[Paper]:
    """Papers citing each ``(paper_id, title)`` in *pairs*, tagged with the cited title."""
    cutoff = _cutoff(days)
    out: list[Paper] = []
    for pid, title in pairs:
        try:
            data = await _s2_request(
                "GET",
                f"{S2_API}/paper/{pid}/citations",
                params={"fields": _PAPER_FIELDS, "limit": per_paper},
            )
        except SearchError as exc:
            logger.warning("Citations of %s failed: %s", pid, exc)
            continue
        items = [it.get("citingPaper") for it in data.get("data", []) if it.get("citingPaper")]
        for p in _parse_items(items):
            if within(p, cutoff):
                out.append(p.model_copy(update={"cites_author_paper": title}))
    return out


async def s2_recommendations(seed_ids: list[str], *, days: int | None, limit: int = 100) -> list[Paper]:
    """Papers similar to the seed papers (S2 Recommendations API)."""
    if not seed_ids:
        return []
    data = await _s2_request(
        "POST",
        S2_RECOMMENDATIONS_API,
        params={"fields": _PAPER_FIELDS, "limit": limit},
        body={"positivePaperIds": seed_ids, "negativePaperIds": []},
    )
    cutoff = _cutoff(days)
    return [p for p in _parse_items(data.get("recommendedPapers", [])) if within(p, cutoff)]


async def s2_fetch(paper_ids: list[str]) -> list[Paper]:
    """Fetch full records by S2 id (batch endpoint). Falls back to the TL;DR when
    the abstract is missing, which is common for paywalled venues."""
    if not paper_ids:
        return []
    data = await _s2_request(
        "POST",
        f"{S2_API}/paper/batch",
        params={"fields": _PAPER_FIELDS + ",tldr"},
        body={"ids": paper_ids},
    )
    return _parse_items([it for it in data if it])


async def fetch_author_papers_and_topics(author_id: str) -> tuple[list[tuple[str, str]], list[str]]:
    """``(paper_id, title)`` pairs for the author's recent + top-cited papers, and the
    S2 fields of study those papers fall under (most common first)."""
    data = await _s2_request(
        "GET",
        f"{S2_API}/author/{author_id}/papers",
        params={"fields": "paperId,title,year,citationCount,s2FieldsOfStudy", "limit": 50},
    )
    items = [p for p in data.get("data", []) if p.get("paperId")]
    recent = sorted(items, key=lambda p: p.get("year") or 0, reverse=True)[:10]
    top = sorted(items, key=lambda p: p.get("citationCount") or 0, reverse=True)[:5]
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for p in recent + top:
        if p["paperId"] not in seen:
            seen.add(p["paperId"])
            pairs.append((p["paperId"], p.get("title") or p["paperId"]))
    counts: dict[str, int] = {}
    for p in items:
        for fos in p.get("s2FieldsOfStudy") or []:
            cat = (fos.get("category") or "").strip()
            if cat:
                counts[cat] = counts.get(cat, 0) + 1
    return pairs, sorted(counts, key=lambda k: -counts[k])


def _parse_items(items: list) -> list[Paper]:
    papers: list[Paper] = []
    for item in items:
        try:
            p = _s2_item_to_paper(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping malformed S2 entry: %s", exc)
            continue
        if p is not None:
            papers.append(p)
    return papers


def _s2_item_to_paper(item: dict) -> Paper | None:
    pid = item.get("paperId") or ""
    title = " ".join((item.get("title") or "").split())
    if not pid or not title:
        return None
    abstract = item.get("abstract") or ""
    if not abstract and (item.get("tldr") or {}).get("text"):
        abstract = "(TL;DR) " + item["tldr"]["text"]
    venue_info = item.get("publicationVenue")
    if not isinstance(venue_info, dict):  # the recommendations API may return a bare venue id
        venue_info = {}
    journal = venue_info.get("name") or item.get("venue") or ""
    published = None
    if item.get("publicationDate"):
        try:
            published = datetime.fromisoformat(item["publicationDate"]).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    elif item.get("year"):
        published = datetime(int(item["year"]), 1, 1, tzinfo=timezone.utc)
    external = item.get("externalIds") or {}
    arxiv_id = external.get("ArXiv") or ""
    doi = external.get("DOI") or ""
    url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else (
        f"https://doi.org/{doi}" if doi else f"https://www.semanticscholar.org/paper/{pid}"
    )
    return Paper(
        id=f"s2:{pid}",
        title=title,
        authors=[a["name"] for a in (item.get("authors") or []) if isinstance(a, dict) and a.get("name")],
        journal=journal,
        abstract=abstract,
        url=url,
        doi=doi,
        arxiv_id=arxiv_id,
        published=published,
        citation_count=int(item.get("citationCount") or 0),
    )


# ── arXiv ─────────────────────────────────────────────────────────────────────

_ATOM = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def arxiv_query(phrases: list[str], categories: list[str], days: int | None) -> str:
    parts = []
    if categories:
        parts.append("(" + " OR ".join(f"cat:{c}" for c in categories) + ")")
    if phrases:
        cleaned = [p.replace('"', "").strip() for p in phrases if p.strip()]
        parts.append("(" + " OR ".join(f'all:"{p}"' for p in cleaned) + ")")
    if days is not None:
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=days)
        parts.append(f"submittedDate:[{start:%Y%m%d}0000 TO {end:%Y%m%d}2359]")
    return " AND ".join(parts)


async def arxiv_search(
    phrases: list[str], categories: list[str], *, days: int | None, limit: int = 200
) -> list[Paper]:
    """Recent arXiv submissions matching any phrase within the given categories."""
    if not phrases and not categories:
        return []
    params = {
        "search_query": arxiv_query(phrases, categories, days),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": min(limit, 500),
    }
    async with _lock("arxiv"):
        await _throttle("arxiv", _ARXIV_MIN_INTERVAL)
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(ARXIV_API, params=params)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SearchError(f"arXiv request failed: {exc}") from exc
    cutoff = _cutoff(days)
    return [p for p in parse_arxiv_feed(resp.text) if within(p, cutoff)]


def parse_arxiv_feed(xml_text: str) -> list[Paper]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SearchError(f"arXiv returned malformed XML: {exc}") from exc
    papers: list[Paper] = []
    for e in root.findall("a:entry", _ATOM):
        raw_id = e.findtext("a:id", default="", namespaces=_ATOM)
        m = re.search(r"abs/([^v\s]+)(v\d+)?$", raw_id)
        if not m:
            continue
        arxiv_id = m.group(1)
        title = " ".join((e.findtext("a:title", default="", namespaces=_ATOM)).split())
        if not title:
            continue
        published = None
        if e.findtext("a:published", namespaces=_ATOM):
            try:
                published = datetime.fromisoformat(
                    e.findtext("a:published", namespaces=_ATOM).replace("Z", "+00:00")
                )
            except ValueError:
                pass
        primary = e.find("arxiv:primary_category", _ATOM)
        cat = primary.get("term", "") if primary is not None else ""
        papers.append(
            Paper(
                id=f"arxiv:{arxiv_id}",
                title=title,
                authors=[
                    a.findtext("a:name", default="", namespaces=_ATOM)
                    for a in e.findall("a:author", _ATOM)
                ],
                journal=f"arXiv ({cat})" if cat else "arXiv",
                abstract=" ".join((e.findtext("a:summary", default="", namespaces=_ATOM)).split()),
                url=f"https://arxiv.org/abs/{arxiv_id}",
                doi=e.findtext("arxiv:doi", default="", namespaces=_ATOM) or "",
                arxiv_id=arxiv_id,
                published=published,
            )
        )
    return papers


# ── Crossref (authors / abstracts missing from S2) ───────────────────────────

def _crossref_to_fields(message: dict) -> dict:
    """Extract ``authors`` and ``abstract`` from a Crossref work record."""
    authors = []
    for a in message.get("author") or []:
        if not isinstance(a, dict):
            continue
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
        if name:
            authors.append(name)
    abstract = re.sub(r"<[^>]+>", " ", message.get("abstract") or "")
    return {"authors": authors, "abstract": " ".join(abstract.split())}


async def crossref_fill(papers: list[Paper]) -> list[Paper]:
    """Return copies of *papers* with authors/abstract filled from Crossref where a DOI
    is known and the field is empty. Papers that cannot be improved are returned as-is."""
    targets = [p for p in papers if p.doi and (not p.authors or not p.abstract)]
    if not targets:
        return papers
    sem = asyncio.Semaphore(4)

    async def one(client: httpx.AsyncClient, p: Paper) -> Paper:
        async with sem:
            try:
                resp = await client.get(f"{CROSSREF_API}/{p.doi}")
                resp.raise_for_status()
                fields = _crossref_to_fields(resp.json().get("message") or {})
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("Crossref lookup failed for %s: %s", p.doi, exc)
                return p
        return p.model_copy(update={
            "authors": p.authors or fields["authors"],
            "abstract": p.abstract or fields["abstract"],
        })

    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": _USER_AGENT}) as client:
        filled = await asyncio.gather(*(one(client, p) for p in targets))
    by_id = {p.id: p for p in filled}
    return [by_id.get(p.id, p) for p in papers]


# ── Merging ───────────────────────────────────────────────────────────────────

def _title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def merge_papers(*lists: list[Paper]) -> list[Paper]:
    """De-duplicate across sources by arXiv id, DOI, then normalised title.

    S2 records win (they carry venue and citation data); an arXiv record fills in
    a missing abstract. ``cites_author_paper`` is kept from whichever record has it.
    """
    by_key: dict[str, Paper] = {}
    order: list[str] = []

    def keys(p: Paper) -> list[str]:
        ks = []
        if p.arxiv_id:
            ks.append("arxiv:" + p.arxiv_id.lower())
        if p.doi:
            ks.append("doi:" + p.doi.lower())
        ks.append("title:" + _title_key(p.title))
        return ks

    for p in [q for lst in lists for q in lst]:
        existing_key = next((k for k in keys(p) if k in by_key), None)
        if existing_key is None:
            for k in keys(p):
                by_key[k] = p
            order.append(keys(p)[0])
            continue
        old = by_key[existing_key]
        keep, other = (old, p) if old.id.startswith("s2:") or not p.id.startswith("s2:") else (p, old)
        merged = keep.model_copy(update={
            "abstract": keep.abstract or other.abstract,
            "arxiv_id": keep.arxiv_id or other.arxiv_id,
            "doi": keep.doi or other.doi,
            "cites_author_paper": keep.cites_author_paper or other.cites_author_paper,
            "citation_count": max(keep.citation_count, other.citation_count),
            "url": keep.url if keep.arxiv_id or not other.arxiv_id else other.url,
        })
        for k in keys(old) + keys(p):
            by_key[k] = merged
    seen: set[int] = set()
    out: list[Paper] = []
    for k in order:
        p = by_key[k]
        if id(p) not in seen:
            seen.add(id(p))
            out.append(p)
    return out
