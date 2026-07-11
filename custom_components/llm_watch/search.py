"""SearXNG client and candidate gathering for search watches."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from .const import FETCH_TIMEOUT, MAX_RESULTS_PER_QUERY
from .helpers import dedupe_urls, site_hosts

_LOGGER = logging.getLogger(__name__)


async def searx_search(
    session: aiohttp.ClientSession, searxng_url: str, query: str
) -> list[str]:
    """Run one query against SearXNG, returning result URLs.

    Requires the JSON format to be enabled in the SearXNG settings
    (search: formats: [html, json]).
    """
    async with asyncio.timeout(FETCH_TIMEOUT):
        resp = await session.get(
            f"{searxng_url.rstrip('/')}/search",
            params={"q": query, "format": "json"},
        )
        resp.raise_for_status()
        data = await resp.json(content_type=None)
    results = data.get("results") or []
    return [r["url"] for r in results[:MAX_RESULTS_PER_QUERY] if r.get("url")]


async def gather_candidates(
    session: aiohttp.ClientSession,
    searxng_url: str,
    queries: list[str],
    sites: str | None,
    max_pages: int,
) -> list[str]:
    """Turn queries into a deduplicated list of candidate page URLs.

    If the watch is restricted to specific sites, each query is scoped to
    each site with the site: operator.
    """
    hosts = site_hosts(sites)
    scoped: list[str] = []
    if hosts:
        for query in queries:
            scoped.extend(f"{query} site:{host}" for host in hosts)
    else:
        scoped = list(queries)

    urls: list[str] = []
    for query in scoped:
        try:
            urls.extend(await searx_search(session, searxng_url, query))
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.warning("SearXNG query '%s' failed: %s", query, err)
    return dedupe_urls(urls, max_pages)
