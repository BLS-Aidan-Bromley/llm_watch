"""Pure helpers for LLM Watch.

Everything in this module is deliberately free of Home Assistant imports
so it can be unit tested standalone.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .const import MAX_CONTENT_CHARS

# Tags that never contain content worth sending to the model.
_STRIP_TAGS = ["script", "style", "noscript", "svg", "iframe", "head", "template"]

EXTRACT_TEMPLATE = (
    "You are a data extraction assistant inside a home automation system. "
    "Below is the text content of a web page and a description of what the "
    "user is looking for. Decide whether the page contains what they want. "
    "Only report items that genuinely match the description; do not pad the "
    "list with loosely related products. Prices must be numbers without "
    "currency symbols. Set in_stock true only if the page indicates the item "
    "can be bought or obtained now; false if it says out of stock, sold out "
    "or unavailable; leave it unset if the page does not say. If nothing "
    "matches, return found=false with an empty items list and a one-sentence "
    "summary of what the page showed instead. Never invent items that are "
    "not on the page.\n\n"
    "The user is looking for: {prompt}\n\n"
    "Page URL: {url}\n\n"
    "Page content:\n{page_text}"
)

QUERY_TEMPLATE = (
    "You write web search queries for a shopping and deals watcher. "
    "Produce 2 or 3 short, distinct search queries (4-8 words each, no "
    "quotes, no site: operators) that would find current offers, product "
    "listings or availability for what the user describes. Focus on pages "
    "that list products with prices.\n\n"
    "The user is looking for: {prompt}"
)


def clean_html(raw: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Reduce an HTML document to readable text for the model."""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max_chars]


def clean_json(raw: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Compact a JSON payload for the model, or fall back to raw text."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw[:max_chars]
    return json.dumps(parsed, ensure_ascii=False, indent=1)[:max_chars]


def looks_like_json(content_type: str | None, body: str) -> bool:
    """Decide whether a response body should be treated as JSON."""
    if content_type and "json" in content_type.lower():
        return True
    stripped = body.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def build_extract_instructions(prompt: str, url: str, page_text: str) -> str:
    """Build the extraction instructions for the AI Task."""
    return EXTRACT_TEMPLATE.format(prompt=prompt, url=url, page_text=page_text)


def build_query_instructions(prompt: str) -> str:
    """Build the query generation instructions for the AI Task."""
    return QUERY_TEMPLATE.format(prompt=prompt)


def parse_queries(content: Any, max_queries: int) -> list[str]:
    """Normalise the query-generation result into a list of strings."""
    data = content
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(data, dict):
        return []
    raw = data.get("queries") or []
    queries: list[str] = []
    for entry in raw:
        if isinstance(entry, dict):
            entry = entry.get("query")
        if isinstance(entry, str) and entry.strip():
            queries.append(entry.strip())
    return queries[:max_queries]


def parse_result(content: Any) -> dict[str, Any]:
    """Normalise the AI Task extraction result.

    Accepts a dict (the usual case) or a JSON string. Raises ValueError
    if the reply is not usable.
    """
    data = content
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError) as err:
            raise ValueError(f"Model did not return valid JSON: {err}") from err
    if not isinstance(data, dict) or "found" not in data:
        raise ValueError("Model reply is missing the \'found\' field")

    items_in = data.get("items") or []
    items: list[dict[str, Any]] = []
    if isinstance(items_in, list):
        for entry in items_in:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            price = entry.get("price")
            if isinstance(price, str):
                match = re.search(r"\d+(?:[.,]\d+)?", price)
                price = float(match.group().replace(",", ".")) if match else None
            in_stock = entry.get("in_stock")
            if in_stock is not None:
                in_stock = bool(in_stock)
            items.append(
                {
                    "name": str(entry.get("name")),
                    "price": price,
                    "availability": entry.get("availability"),
                    "in_stock": in_stock,
                    "detail": entry.get("detail"),
                }
            )

    return {
        "found": bool(data["found"]),
        "summary": str(data.get("summary") or ""),
        "items": items,
    }


def filter_items(
    items: list[dict[str, Any]],
    require_in_stock: bool,
    max_price: float | None,
) -> list[dict[str, Any]]:
    """Apply the watch criteria to extracted items."""
    out = []
    for item in items:
        if require_in_stock and item.get("in_stock") is not True:
            continue
        if max_price is not None:
            price = item.get("price")
            if price is None or price > max_price:
                continue
        out.append(item)
    return out


def best_price(items: list[dict[str, Any]]) -> float | None:
    """Lowest price among items that carry one."""
    prices = [i["price"] for i in items if i.get("price") is not None]
    return min(prices) if prices else None


def dedupe_urls(urls: list[str], limit: int) -> list[str]:
    """Deduplicate URLs, keeping order, at most one per host beyond dedupe."""
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        key = url.split("#")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def site_hosts(sites: str | None) -> list[str]:
    """Parse the comma-separated sites field into bare hostnames."""
    if not sites:
        return []
    hosts = []
    for part in sites.split(","):
        part = part.strip()
        if not part:
            continue
        if "//" in part:
            part = urlparse(part).netloc or part
        hosts.append(part.removeprefix("www."))
    return hosts
