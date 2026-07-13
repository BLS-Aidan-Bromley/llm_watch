"""Shopify storefront helpers.

Shopify stores expose a public products.json per collection and a per-product
.json, both without auth. When a candidate URL is a Shopify collection or
product page, we can read exact handles, prices and stock instead of asking
the model to read rendered HTML. This is the reliable path for retail sites.
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


def collection_handle(url: str) -> str | None:
    """Return the collection handle if the URL is a Shopify collection page."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "collections" and parts[1] != "":
        # /collections/<handle> but not a /products/ sub-path
        if "products" not in parts:
            return parts[1]
    return None


def product_handle(url: str) -> str | None:
    """Return the product handle if the URL is a Shopify product page."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if "products" in parts:
        i = parts.index("products")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def _variant_stock(product: dict[str, Any]) -> tuple[float | None, bool | None]:
    """Lowest variant price and whether any variant is available."""
    variants = product.get("variants") or []
    prices = []
    available = None
    for v in variants:
        try:
            prices.append(float(v["price"]))
        except (KeyError, TypeError, ValueError):
            pass
        if v.get("available") is True:
            available = True
        elif available is None and v.get("available") is False:
            available = False
    return (min(prices) if prices else None), available


def _item_from_product(base: str, product: dict[str, Any]) -> dict[str, Any]:
    price, available = _variant_stock(product)
    handle = product.get("handle", "")
    return {
        "name": product.get("title") or handle,
        "price": price,
        "availability": "in stock" if available else (
            "out of stock" if available is False else None
        ),
        "in_stock": available,
        "detail": None,
        "link": f"{base}/products/{handle}" if handle else None,
        "source": f"{base}/products/{handle}" if handle else base,
        "verified": True,  # came straight from the store's own JSON
    }


async def fetch_collection_products(
    session: aiohttp.ClientSession, url: str, limit: int
) -> list[dict[str, Any]] | None:
    """Read a Shopify collection's products.json. None if not Shopify/unreadable."""
    handle = collection_handle(url)
    if not handle:
        return None
    base = _base(url)
    endpoint = f"{base}/collections/{handle}/products.json?limit={limit}"
    try:
        async with asyncio.timeout(FETCH_TIMEOUT):
            resp = await session.get(endpoint, headers=_HEADERS)
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as err:
        _LOGGER.debug("Shopify collection fetch failed for %s: %s", endpoint, err)
        return None
    products = data.get("products")
    if not isinstance(products, list):
        return None
    return [_item_from_product(base, p) for p in products]


async def verify_product(
    session: aiohttp.ClientSession, url: str
) -> dict[str, Any] | None:
    """Read a single Shopify product's .json for exact price and stock.

    Returns a verified item, or None if the URL isn't a readable Shopify
    product page.
    """
    handle = product_handle(url)
    if not handle:
        return None
    base = _base(url)
    endpoint = f"{base}/products/{handle}.json"
    try:
        async with asyncio.timeout(FETCH_TIMEOUT):
            resp = await session.get(endpoint, headers=_HEADERS)
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as err:
        _LOGGER.debug("Shopify product fetch failed for %s: %s", endpoint, err)
        return None
    product = data.get("product")
    if not isinstance(product, dict):
        return None
    return _item_from_product(base, product)
