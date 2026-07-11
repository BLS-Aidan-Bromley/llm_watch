"""Coordinators for LLM Watch page and search watches."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.components.ai_task import async_generate_data
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    MAX_CONTENT_CHARS,
    CONF_AI_TASK_ENTITY,
    CONF_BACKEND,
    CONF_MAX_PRICE,
    CONF_MODE,
    CONF_NAME,
    CONF_PROMPT,
    CONF_BLOCKLIST,
    CONF_REQUIRE_IN_STOCK,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_SHOPPING_ONLY,
    CONF_SITES,
    CONF_URL,
    DEFAULT_BLOCKLIST,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    EVENT_FOUND,
    EVENT_PRICE_DROP,
    FETCH_TIMEOUT,
    MAX_PAGES,
    MAX_QUERIES,
    MODE_AUTO,
    MODE_JSON,
    SUBENTRY_SEARCH_WATCH,
)
from .helpers import (
    best_price,
    build_extract_instructions,
    build_query_instructions,
    clean_html,
    clean_json,
    filter_items,
    looks_like_json,
    parse_blocklist,
    parse_queries,
    parse_result,
)
from .search import backend_ready, gather_candidates

_LOGGER = logging.getLogger(__name__)

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Structured output definitions handed to the AI Task provider. Selector
# based, so every provider (Ollama, OpenAI, Anthropic, Google, ...) converts
# them to its own structured-output format.
RESULT_STRUCTURE = vol.Schema(
    {
        vol.Required(
            "found",
            description="Whether the page contains what the user asked for",
        ): selector.selector({"boolean": {}}),
        vol.Required(
            "summary",
            description="One or two sentences on what was (or was not) found",
        ): selector.selector({"text": {}}),
        vol.Required(
            "items",
            description="The matching items. Empty list if nothing matches.",
        ): selector.ObjectSelector(
            {
                "multiple": True,
                "fields": {
                    "name": {"required": True, "selector": {"text": {}}},
                    "price": {
                        "selector": {"number": {"mode": "box", "step": 0.01}}
                    },
                    "availability": {"selector": {"text": {}}},
                    "in_stock": {"selector": {"boolean": {}}},
                    "detail": {"selector": {"text": {}}},
                },
            }
        ),
    }
)

QUERY_STRUCTURE = vol.Schema(
    {
        vol.Required(
            "queries",
            description="Two or three short, distinct web search queries",
        ): selector.ObjectSelector(
            {
                "multiple": True,
                "fields": {
                    "query": {"required": True, "selector": {"text": {}}},
                },
            }
        ),
    }
)


async def fetch_page_text(
    session: aiohttp.ClientSession, url: str, mode: str = MODE_AUTO
) -> str:
    """Fetch a URL and reduce it to model-readable text."""
    async with asyncio.timeout(FETCH_TIMEOUT):
        resp = await session.get(url, headers=_FETCH_HEADERS)
        resp.raise_for_status()
        body = await resp.text()
        content_type = resp.headers.get("Content-Type")

    if mode == MODE_JSON or (mode == MODE_AUTO and looks_like_json(content_type, body)):
        page_text = clean_json(body)
    else:
        page_text = clean_html(body)

    if len(page_text.strip()) < 50:
        raise UpdateFailed(
            "Page produced almost no text. It is probably rendered with "
            "JavaScript; point the watch at the site's JSON API instead."
        )
    return page_text


async def _extract(
    hass: HomeAssistant,
    name: str,
    entity_id: str | None,
    prompt: str,
    url: str,
    page_text: str,
    shopping: bool = False,
) -> dict[str, Any]:
    """One extraction call against the AI Task provider."""
    task_result = await async_generate_data(
        hass,
        task_name=f"LLM Watch: {name}",
        entity_id=entity_id or None,
        instructions=build_extract_instructions(prompt, url, page_text, shopping),
        structure=RESULT_STRUCTURE,
    )
    return parse_result(task_result.data)


def _apply_criteria(result: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Filter items by the watch criteria and recompute found."""
    has_criteria = bool(config.get(CONF_REQUIRE_IN_STOCK)) or (
        config.get(CONF_MAX_PRICE) is not None
    )
    items = filter_items(
        result["items"],
        bool(config.get(CONF_REQUIRE_IN_STOCK)),
        config.get(CONF_MAX_PRICE),
    )
    return {
        "found": bool(items) if has_criteria else result["found"],
        "summary": result["summary"],
        "items": items,
    }


async def run_page_check(
    hass: HomeAssistant, config: dict[str, Any], hub: dict[str, Any]
) -> dict[str, Any]:
    """Check a single page watch. Shared with the config flow."""
    session = async_get_clientsession(hass)
    page_text = await fetch_page_text(
        session, config[CONF_URL], config.get(CONF_MODE, MODE_AUTO)
    )
    entity_id = config.get(CONF_AI_TASK_ENTITY) or hub.get(CONF_AI_TASK_ENTITY)
    result = await _extract(
        hass,
        config[CONF_NAME],
        entity_id,
        config[CONF_PROMPT],
        config[CONF_URL],
        page_text,
        bool(config.get(CONF_SHOPPING_ONLY)),
    )
    for item in result["items"]:
        item["source"] = config[CONF_URL]
    result = _apply_criteria(result, config)
    result["checked_at"] = dt_util.now().isoformat()
    return result


async def run_search_check(
    hass: HomeAssistant, config: dict[str, Any], hub: dict[str, Any]
) -> dict[str, Any]:
    """Check a search watch: queries -> SearXNG -> pages -> extraction."""
    if backend_ready(hub) is None:
        raise UpdateFailed(
            "The search backend on the LLM Watch hub is not configured "
            "(pick a backend and fill in its URL or API key). Reconfigure "
            "the integration."
        )
    session = async_get_clientsession(hass)
    entity_id = config.get(CONF_AI_TASK_ENTITY) or hub.get(CONF_AI_TASK_ENTITY)
    name = config[CONF_NAME]
    prompt = config[CONF_PROMPT]

    shopping = bool(config.get(CONF_SHOPPING_ONLY))
    query_result = await async_generate_data(
        hass,
        task_name=f"LLM Watch queries: {name}",
        entity_id=entity_id or None,
        instructions=build_query_instructions(prompt, shopping),
        structure=QUERY_STRUCTURE,
    )
    queries = parse_queries(query_result.data, MAX_QUERIES)
    if not queries:
        # Model failed to produce queries; fall back to the raw description.
        queries = [prompt[:100]]

    blocklist = (
        parse_blocklist(config.get(CONF_BLOCKLIST), DEFAULT_BLOCKLIST)
        if shopping
        else None
    )
    candidates = await gather_candidates(
        session, hub, queries, config.get(CONF_SITES), MAX_PAGES, blocklist
    )
    if not candidates:
        raise UpdateFailed(
            "The search returned no results. Check the backend settings on "
            "the hub (URL / API key) and that the description is searchable."
        )

    all_items: list[dict[str, Any]] = []
    summaries: list[str] = []
    pages_checked = 0
    for cand in candidates:
        url = cand["url"]
        try:
            if cand.get("content") and len(cand["content"].strip()) >= 50:
                # Backend supplied extracted content (Tavily): no fetch needed.
                page_text = cand["content"][:MAX_CONTENT_CHARS]
            else:
                page_text = await fetch_page_text(session, url)
            result = await _extract(
                hass, name, entity_id, prompt, url, page_text, shopping
            )
        except (TimeoutError, aiohttp.ClientError, UpdateFailed, ValueError) as err:
            _LOGGER.debug("Skipping candidate %s: %s", url, err)
            continue
        pages_checked += 1
        if result["found"]:
            for item in result["items"]:
                item["source"] = url
            all_items.extend(result["items"])
            summaries.append(result["summary"])

    if pages_checked == 0:
        raise UpdateFailed(
            "None of the search results could be read (JavaScript-only pages "
            "or fetch failures)."
        )

    aggregated = {
        "found": bool(all_items),
        "summary": " ".join(summaries[:3]) if summaries else "Nothing matched.",
        "items": all_items,
    }
    aggregated = _apply_criteria(aggregated, config)
    aggregated["queries"] = queries
    aggregated["pages_checked"] = pages_checked
    aggregated["checked_at"] = dt_util.now().isoformat()
    return aggregated


class LlmWatchCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Runs one watch (page or search subentry) on a schedule."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        seed: dict[str, Any] | None = None,
        on_result=None,
    ) -> None:
        self.entry = entry
        self.subentry = subentry
        self._seed = seed or {}
        self._on_result = on_result
        hours = subentry.data.get(
            CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {subentry.data[CONF_NAME]}",
            update_interval=timedelta(hours=hours),
        )

    @property
    def config(self) -> dict[str, Any]:
        return dict(self.subentry.data)

    @property
    def hub(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def watch_name(self) -> str:
        return self.config[CONF_NAME]

    @property
    def is_search(self) -> bool:
        return self.subentry.subentry_type == SUBENTRY_SEARCH_WATCH

    async def _async_update_data(self) -> dict[str, Any]:
        # After a restart self.data is empty; the persisted seed keeps the
        # found/price baseline so events don't misfire or get lost.
        previous = self.data or self._seed or {}
        previously_found = bool(previous.get("found"))
        previous_best = best_price(previous.get("items") or [])
        try:
            if self.is_search:
                result = await run_search_check(self.hass, self.config, self.hub)
            else:
                result = await run_page_check(self.hass, self.config, self.hub)
        except UpdateFailed:
            raise
        except (TimeoutError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"Page fetch failed: {err}") from err
        except HomeAssistantError as err:
            raise UpdateFailed(f"AI task failed: {err}") from err
        except ValueError as err:
            raise UpdateFailed(str(err)) from err

        event_base = {
            "name": self.watch_name,
            "summary": result["summary"],
            "items": result["items"],
        }
        if result["found"] and not previously_found:
            self.hass.bus.async_fire(EVENT_FOUND, event_base)

        new_best = best_price(result["items"])
        if (
            previous_best is not None
            and new_best is not None
            and new_best < previous_best
        ):
            self.hass.bus.async_fire(
                EVENT_PRICE_DROP,
                {**event_base, "old_price": previous_best, "new_price": new_best},
            )
        if self._on_result is not None:
            await self._on_result(self.subentry.subentry_id, result)
        return result
