"""Search backends (SearXNG, Tavily, Brave) and candidate gathering."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TypedDict

import aiohttp

from .const import (
    BACKEND_BRAVE,
    BACKEND_SEARXNG,
    BACKEND_TAVILY,
    CONF_BACKEND,
    CONF_BRAVE_API_KEY,
    CONF_SEARXNG_URL,
    CONF_TAVILY_API_KEY,
    FETCH_TIMEOUT,
    MAX_RESULTS_PER_QUERY,
)
from .helpers import site_hosts

_LOGGER = logging.getLogger(__name__)


class Candidate(TypedDict):
    """A candidate page: URL plus extracted content when the backend has it."""

    url: str
    content: str | None


async def _searx_search(
    session: aiohttp.ClientSession, hub: dict[str, Any], query: str, hosts: list[str]
) -> list[Candidate]:
    """SearXNG: self-hosted; needs JSON format enabled in settings.yml."""
    base = hub[CONF_SEARXNG_URL].rstrip("/")
    if hosts:
        query = f"{query} site:{hosts[0]}" if len(hosts) == 1 else query
    async with asyncio.timeout(FETCH_TIMEOUT):
        resp = await session.get(
            f"{base}/search", params={"q": query, "format": "json"}
        )
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    return [
        {"url": r["url"], "content": None}
        for r in (data.get("results") or [])[:MAX_RESULTS_PER_QUERY]
        if r.get("url")
    ]


async def _tavily_search(
    session: aiohttp.ClientSession, hub: dict[str, Any], query: str, hosts: list[str]
) -> list[Candidate]:
    """Tavily: cloud API for AI agents; returns extracted page content too."""
    body: dict[str, Any] = {
        "query": query,
        "max_results": MAX_RESULTS_PER_QUERY,
        "include_raw_content": True,
    }
    if hosts:
        body["include_domains"] = hosts
    async with asyncio.timeout(FETCH_TIMEOUT):
        resp = await session.post(
            "https://api.tavily.com/search",
            json=body,
            headers={"Authorization": f"Bearer {hub[CONF_TAVILY_API_KEY]}"},
        )
        resp.raise_for_status()
        data = await resp.json()
    out: list[Candidate] = []
    for r in (data.get("results") or [])[:MAX_RESULTS_PER_QUERY]:
        if not r.get("url"):
            continue
        content = r.get("raw_content") or r.get("content") or None
        out.append({"url": r["url"], "content": content})
    return out


async def _brave_search(
    session: aiohttp.ClientSession, hub: dict[str, Any], query: str, hosts: list[str]
) -> list[Candidate]:
    """Brave Search API: independent index; results only, no content."""
    if hosts:
        query = f"{query} site:{hosts[0]}" if len(hosts) == 1 else query
    async with asyncio.timeout(FETCH_TIMEOUT):
        resp = await session.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": MAX_RESULTS_PER_QUERY},
            headers={
                "X-Subscription-Token": hub[CONF_BRAVE_API_KEY],
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        data = await resp.json()
    results = (data.get("web") or {}).get("results") or []
    return [
        {"url": r["url"], "content": None}
        for r in results[:MAX_RESULTS_PER_QUERY]
        if r.get("url")
    ]


_BACKENDS = {
    BACKEND_SEARXNG: _searx_search,
    BACKEND_TAVILY: _tavily_search,
    BACKEND_BRAVE: _brave_search,
}


def backend_ready(hub: dict[str, Any]) -> str | None:
    """Return the usable backend name, or None if details are missing.

    If no backend was chosen (older hubs), infer it from whichever
    detail is configured.
    """
    needed = {
        BACKEND_SEARXNG: CONF_SEARXNG_URL,
        BACKEND_TAVILY: CONF_TAVILY_API_KEY,
        BACKEND_BRAVE: CONF_BRAVE_API_KEY,
    }
    backend = hub.get(CONF_BACKEND)
    if backend in needed:
        return backend if hub.get(needed[backend]) else None
    for name, field in needed.items():
        if hub.get(field):
            return name
    return None


async def gather_candidates(
    session: aiohttp.ClientSession,
    hub: dict[str, Any],
    queries: list[str],
    sites: str | None,
    max_pages: int,
) -> list[Candidate]:
    """Turn queries into a deduplicated list of candidate pages.

    Site restrictions use the backend's native domain filter where it has one
    (Tavily), otherwise the site: operator; with several sites on a site:
    backend, each query is scoped to each site.
    """
    backend = backend_ready(hub)
    if backend is None:
        raise ValueError("Search backend is not configured")
    search = _BACKENDS[backend]
    hosts = site_hosts(sites)

    # For site: backends with multiple hosts, fan queries out per host.
    scoped: list[tuple[str, list[str]]] = []
    if hosts and len(hosts) > 1 and backend != BACKEND_TAVILY:
        for query in queries:
            scoped.extend((query, [host]) for host in hosts)
    else:
        scoped = [(query, hosts) for query in queries]

    seen: set[str] = set()
    out: list[Candidate] = []
    for query, query_hosts in scoped:
        try:
            results = await search(session, hub, query, query_hosts)
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.warning("%s query '%s' failed: %s", backend, query, err)
            continue
        for cand in results:
            key = cand["url"].split("#")[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)
            if len(out) >= max_pages:
                return out
    return out
