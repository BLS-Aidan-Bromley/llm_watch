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
    CONF_MAX_ROUNDS,
    CONF_REQUIRE_IN_STOCK,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_SHOPPING_ONLY,
    CONF_VERIFY,
    CONF_SITES,
    CONF_URL,
    DEFAULT_BLOCKLIST,
    DEFAULT_MAX_ROUNDS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DEFAULT_VERIFY,
    DOMAIN,
    EVENT_FOUND,
    EVENT_PRICE_DROP,
    FETCH_TIMEOUT,
    MAX_CANDIDATES_PER_ROUND,
    MAX_PAGES,
    MAX_QUERIES,
    MAX_VERIFIED_ITEMS,
    MODE_AUTO,
    MODE_JSON,
    SUBENTRY_SEARCH_WATCH,
)
from .helpers import (
    best_price,
    build_extract_instructions,
    build_query_instructions,
    build_verify_instructions,
    clean_html,
    clean_json,
    filter_items,
    looks_like_json,
    parse_blocklist,
    parse_queries,
    parse_result,
    parse_verification,
    price_agrees,
)
from .search import backend_ready, gather_candidates
from .shopify import fetch_collection_products, verify_product

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
                    "link_ref": {
                        "selector": {"number": {"mode": "box", "step": 1}}
                    },
                },
            }
        ),
    }
)

VERIFY_STRUCTURE = vol.Schema(
    {
        vol.Required(
            "matches",
            description="Whether this product page matches the requirement",
        ): selector.selector({"boolean": {}}),
        vol.Required(
            "name", description="The product name as shown on the page"
        ): selector.selector({"text": {}}),
        vol.Required(
            "price", description="The price as a number, no currency symbol"
        ): selector.selector({"number": {"mode": "box", "step": 0.01}}),
        vol.Required(
            "in_stock",
            description="True if buyable now, false if sold out",
        ): selector.selector({"boolean": {}}),
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
) -> tuple[str, list[str]]:
    """Fetch a URL and reduce it to model-readable text plus a link list."""
    async with asyncio.timeout(FETCH_TIMEOUT):
        resp = await session.get(url, headers=_FETCH_HEADERS)
        resp.raise_for_status()
        body = await resp.text()
        content_type = resp.headers.get("Content-Type")

    if mode == MODE_JSON or (mode == MODE_AUTO and looks_like_json(content_type, body)):
        page_text, links = clean_json(body), []
    else:
        page_text, links = clean_html(body, url)

    if len(page_text.strip()) < 50:
        raise UpdateFailed(
            "Page produced almost no text. It is probably rendered with "
            "JavaScript; point the watch at the site's JSON API instead."
        )
    return page_text, links


async def _extract(
    hass: HomeAssistant,
    name: str,
    entity_id: str | None,
    prompt: str,
    url: str,
    page_text: str,
    links: list[str] | None = None,
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
    return parse_result(task_result.data, links)


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
    page_text, links = await fetch_page_text(
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
        links=links,
        shopping=bool(config.get(CONF_SHOPPING_ONLY)),
    )
    for item in result["items"]:
        item["source"] = config[CONF_URL]
    result = _apply_criteria(result, config)
    result["checked_at"] = dt_util.now().isoformat()
    return result


async def _discover(
    hass, session, hub, config, entity_id, queries, shopping
) -> list[dict[str, Any]]:
    """Pass one: produce candidate items from search results.

    Shopify collection pages are read from products.json (exact data). Other
    pages are read by the model. Each candidate carries its own product link
    where possible, which pass two will visit.
    """
    blocklist = (
        parse_blocklist(config.get(CONF_BLOCKLIST), DEFAULT_BLOCKLIST)
        if shopping
        else None
    )
    candidates = await gather_candidates(
        session, hub, queries, config.get(CONF_SITES), MAX_PAGES, blocklist
    )
    items: list[dict[str, Any]] = []
    pages_read = 0
    for cand in candidates:
        url = cand["url"]

        # Shopify collection: pull structured products directly.
        shop_items = await fetch_collection_products(
            session, url, MAX_CANDIDATES_PER_ROUND
        )
        if shop_items is not None:
            pages_read += 1
            items.extend(shop_items)
            continue

        # Otherwise read the page (backend content, or fetch) with the model.
        try:
            if cand.get("content") and len(cand["content"].strip()) >= 50:
                page_text, links = cand["content"][:MAX_CONTENT_CHARS], []
            else:
                page_text, links = await fetch_page_text(session, url)
            result = await _extract(
                hass, config[CONF_NAME], entity_id, config[CONF_PROMPT],
                url, page_text, links=links, shopping=shopping,
            )
        except (TimeoutError, aiohttp.ClientError, UpdateFailed, ValueError) as err:
            _LOGGER.debug("Discovery skipped %s: %s", url, err)
            continue
        pages_read += 1
        if result["found"]:
            for item in result["items"]:
                item.setdefault("source", url)
                item.setdefault("link", None)
                item["verified"] = False
            items.extend(result["items"])
    return items, pages_read


async def _verify_one(
    hass, session, config, entity_id, item
) -> dict[str, Any] | None:
    """Pass two: confirm one candidate on its own product page.

    Returns the item (with confirmed price/stock) if it checks out, else None.
    Items that came straight from Shopify JSON are already trustworthy.
    """
    if item.get("verified"):
        return item

    link = item.get("link") or item.get("source")
    if not link:
        return None  # nothing to verify against -> drop

    # Shopify product page: authoritative JSON, no model needed.
    shop_item = await verify_product(session, link)
    if shop_item is not None:
        if not price_agrees(item.get("price"), shop_item.get("price")):
            _LOGGER.debug("Price disagreement dropped: %s", link)
            return None
        return shop_item

    # Generic product page: fetch it and ask the model to confirm.
    try:
        page_text, _ = await fetch_page_text(session, link)
    except (TimeoutError, aiohttp.ClientError, UpdateFailed) as err:
        _LOGGER.debug("Verify fetch failed, dropping %s: %s", link, err)
        return None
    try:
        task = await async_generate_data(
            hass,
            task_name=f"LLM Watch verify: {config[CONF_NAME]}",
            entity_id=entity_id or None,
            instructions=build_verify_instructions(
                config[CONF_PROMPT], link, page_text
            ),
            structure=VERIFY_STRUCTURE,
        )
        verdict = parse_verification(task.data)
    except (HomeAssistantError, ValueError) as err:
        _LOGGER.debug("Verify model failed, dropping %s: %s", link, err)
        return None

    if not verdict["matches"]:
        return None
    if not price_agrees(item.get("price"), verdict.get("price")):
        return None
    return {
        "name": verdict["name"] or item.get("name"),
        "price": verdict.get("price") if verdict.get("price") is not None else item.get("price"),
        "availability": "in stock" if verdict.get("in_stock") else (
            "out of stock" if verdict.get("in_stock") is False else None
        ),
        "in_stock": verdict.get("in_stock"),
        "detail": item.get("detail"),
        "link": link,
        "source": item.get("source", link),
        "verified": True,
    }


async def run_search_check(
    hass: HomeAssistant, config: dict[str, Any], hub: dict[str, Any]
) -> dict[str, Any]:
    """Two-pass search watch: discover candidates, verify each, retry if none.

    Deliberately thorough, not fast: every surfaced item has been confirmed
    on its own product page for price and stock. If a round yields nothing
    verified, it re-searches with fresh queries up to a bounded number of
    rounds, then reports only what passed (or nothing).
    """
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
    verify = bool(config.get(CONF_VERIFY, DEFAULT_VERIFY))
    max_rounds = int(config.get(CONF_MAX_ROUNDS, DEFAULT_MAX_ROUNDS))

    verified: list[dict[str, Any]] = []
    all_queries: list[str] = []
    rounds_run = 0
    pages_total = 0
    tried_dropped = 0

    for round_no in range(1, max_rounds + 1):
        rounds_run = round_no
        avoid = ""
        if all_queries:
            avoid = (
                " Avoid repeating these earlier queries, try different angles, "
                f"retailers or wording: {'; '.join(all_queries)}."
            )
        query_result = await async_generate_data(
            hass,
            task_name=f"LLM Watch queries: {name} (round {round_no})",
            entity_id=entity_id or None,
            instructions=build_query_instructions(prompt, shopping) + avoid,
            structure=QUERY_STRUCTURE,
        )
        queries = parse_queries(query_result.data, MAX_QUERIES) or [prompt[:100]]
        all_queries.extend(queries)

        candidates, pages_read = await _discover(
            hass, session, hub, config, entity_id, queries, shopping
        )
        pages_total += pages_read

        # Apply the user's criteria before spending verification calls.
        candidates = filter_items(
            candidates,
            bool(config.get(CONF_REQUIRE_IN_STOCK)),
            config.get(CONF_MAX_PRICE),
        )

        if not verify:
            verified.extend(candidates)
        else:
            for item in candidates:
                confirmed = await _verify_one(
                    hass, session, config, entity_id, item
                )
                if confirmed is None:
                    tried_dropped += 1
                    continue
                # Re-apply criteria to the confirmed figures.
                kept = filter_items(
                    [confirmed],
                    bool(config.get(CONF_REQUIRE_IN_STOCK)),
                    config.get(CONF_MAX_PRICE),
                )
                verified.extend(kept)
                if len(verified) >= MAX_VERIFIED_ITEMS:
                    break

        # De-duplicate by link/name.
        seen: set = set()
        deduped: list[dict[str, Any]] = []
        for it in verified:
            key = it.get("link") or it.get("name")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(it)
        verified = deduped

        if verified:
            break  # got something confirmed; stop early

    found = bool(verified)
    if found:
        summary = (
            f"Verified {len(verified)} item(s) across {rounds_run} search "
            f"round(s)."
        )
    else:
        summary = (
            f"Nothing could be verified after {rounds_run} round(s); "
            f"{tried_dropped} candidate(s) failed price/stock checks or "
            "could not be read."
        )

    return {
        "found": found,
        "summary": summary,
        "items": verified[:MAX_VERIFIED_ITEMS],
        "queries": all_queries,
        "rounds_run": rounds_run,
        "pages_checked": pages_total,
        "verified": verify,
        "checked_at": dt_util.now().isoformat(),
    }


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
