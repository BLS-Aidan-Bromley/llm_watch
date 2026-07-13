"""WooCommerce Store API helpers.

WooCommerce sites expose a public, no-auth Store API at
/wp-json/wc/store/v1/products giving exact names, prices, stock and
permalinks. Like the Shopify path, this replaces model guesswork with the
store's own data. Prices arrive in minor units (e.g. "63000" with
currency_minor_unit 2 = 630.00).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import aiohttp

from .const import FETCH_TIMEOUT

_LOGGER = logging.getLogger(__name__)

_HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible)"}


def _base(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def product_slug(url: str) -> str | None:
    """Slug from a WooCommerce product permalink (default /product/<slug>/)."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if "product" in parts:
        i = parts.index("product")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def _price_of(product: dict[str, Any]) -> float | None:
    prices = product.get("prices") or {}
    raw = prices.get("price")
    if raw in (None, ""):
        return None
    try:
        minor = int(prices.get("currency_minor_unit", 2))
        return round(float(raw) / (10**minor), 2)
    except (TypeError, ValueError):
        return None


def _item_from_product(product: dict[str, Any]) -> dict[str, Any]:
    in_stock = product.get("is_in_stock")
    if in_stock is not None:
        in_stock = bool(in_stock)
    link = product.get("permalink")
    return {
        "name": product.get("name") or "",
        "price": _price_of(product),
        "availability": "in stock" if in_stock else (
            "out of stock" if in_stock is False else None
        ),
        "in_stock": in_stock,
        "detail": None,
        "link": link,
        "source": link,
        "verified": True,  # store's own API data
    }


async def _store_api(
    session: aiohttp.ClientSession, base: str, params: dict[str, Any]
) -> list[dict[str, Any]] | None:
    """Call the Store API; None if this isn't a readable WooCommerce site."""
    endpoint = f"{base}/wp-json/wc/store/v1/products"
    try:
        async with asyncio.timeout(FETCH_TIMEOUT):
            resp = await session.get(endpoint, params=params, headers=_HEADERS)
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as err:
        _LOGGER.debug("WooCommerce Store API failed for %s: %s", endpoint, err)
        return None
    if not isinstance(data, list):
        return None
    return data


async def search_products(
    session: aiohttp.ClientSession, url: str, search: str, limit: int
) -> list[dict[str, Any]] | None:
    """Search a WooCommerce site's products. None if not WooCommerce."""
    products = await _store_api(
        session, _base(url), {"search": search, "per_page": limit}
    )
    if products is None:
        return None
    return [_item_from_product(p) for p in products if isinstance(p, dict)]


async def verify_product(
    session: aiohttp.ClientSession, url: str
) -> dict[str, Any] | None:
    """Confirm one product by its permalink slug. None if not resolvable."""
    slug = product_slug(url)
    if not slug:
        return None
    products = await _store_api(session, _base(url), {"slug": slug})
    if not products:
        return None
    return _item_from_product(products[0])
