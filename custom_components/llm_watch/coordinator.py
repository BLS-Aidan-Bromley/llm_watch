"""Coordinator for LLM Watch."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MODE,
    CONF_MODEL,
    CONF_NAME,
    CONF_OLLAMA_URL,
    CONF_PROMPT,
    CONF_SCAN_INTERVAL_HOURS,
    CONF_URL,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    EVENT_FOUND,
    FETCH_TIMEOUT,
    MODE_AUTO,
    MODE_JSON,
    OLLAMA_TIMEOUT,
)
from .helpers import (
    RESULT_SCHEMA,
    build_messages,
    clean_html,
    clean_json,
    looks_like_json,
    parse_result,
)

_LOGGER = logging.getLogger(__name__)

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


async def run_check(
    session: aiohttp.ClientSession, config: dict[str, Any]
) -> dict[str, Any]:
    """Fetch the page and ask the model about it. Shared with the config flow."""
    url: str = config[CONF_URL]
    mode: str = config.get(CONF_MODE, MODE_AUTO)
    ollama_url: str = config.get(CONF_OLLAMA_URL, DEFAULT_OLLAMA_URL).rstrip("/")
    model: str = config.get(CONF_MODEL, DEFAULT_MODEL)
    prompt: str = config[CONF_PROMPT]

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

    payload = {
        "model": model,
        "messages": build_messages(prompt, url, page_text),
        "format": RESULT_SCHEMA,
        "stream": False,
        "options": {"temperature": 0},
    }
    async with asyncio.timeout(OLLAMA_TIMEOUT):
        resp = await session.post(f"{ollama_url}/api/chat", json=payload)
        resp.raise_for_status()
        reply = await resp.json()

    content = (reply.get("message") or {}).get("content", "")
    result = parse_result(content)
    result["checked_at"] = dt_util.now().isoformat()
    return result


class LlmWatchCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Runs one watch on a schedule."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        config = {**entry.data, **entry.options}
        hours = config.get(CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {config[CONF_NAME]}",
            update_interval=timedelta(hours=hours),
        )

    @property
    def config(self) -> dict[str, Any]:
        """Merged config; options win so edits apply without re-adding."""
        return {**self.entry.data, **self.entry.options}

    @property
    def watch_name(self) -> str:
        return self.config[CONF_NAME]

    async def _async_update_data(self) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        previously_found = bool((self.data or {}).get("found"))
        try:
            result = await run_check(session, self.config)
        except UpdateFailed:
            raise
        except (TimeoutError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"Request failed: {err}") from err
        except ValueError as err:
            raise UpdateFailed(str(err)) from err

        if result["found"] and not previously_found:
            self.hass.bus.async_fire(
                EVENT_FOUND,
                {
                    "name": self.watch_name,
                    "url": self.config[CONF_URL],
                    "summary": result["summary"],
                    "items": result["items"],
                },
            )
        return result
