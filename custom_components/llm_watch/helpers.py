"""Pure helpers for LLM Watch.

Everything in this module is deliberately free of Home Assistant imports
so it can be unit tested standalone.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .const import MAX_CONTENT_CHARS, MAX_LINKS

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
    "or unavailable; leave it unset if the page does not say. "
    "Never write out a URL yourself in any field. Instead, when the page "
    "content includes a LINKS list (numbered L1, L2, ...), set each item's "
    "link_ref to the number of the link that points to that specific "
    "product's own page. Choose the most specific product link, not a "
    "category or listing link. If no link clearly belongs to the item, leave "
    "link_ref unset. {shopping_rule}"
    "If nothing matches, return found=false with an empty items list and a "
    "one-sentence summary of what the page showed instead. Never invent items "
    "that are not on the page.\n\n"
    "The user is looking for: {prompt}\n\n"
    "Page URL: {url}\n\n"
    "Page content:\n{page_text}"
)

SHOPPING_RULE = (
    "This is a shopping search: only report items from a page where the item "
    "can actually be bought, i.e. a retailer product or listing page. If this "
    "page is a forum or discussion thread, a review or roundup article, a "
    "'best of' or recommendations listicle, a news article, or any page that "
    "only talks about products rather than selling them, return found=false "
    "with an empty items list, whatever products it mentions. "
)

QUERY_TEMPLATE = (
    "You write web search queries for a shopping and deals watcher. "
    "Produce 2 or 3 short, distinct search queries (4-8 words each, no "
    "quotes, no site: operators) that would find {target} for what the user "
    "describes.\n\n"
    "The user is looking for: {prompt}"
)
QUERY_TARGET_SHOPPING = (
    "retailer product pages where the item can be bought, with prices and "
    "stock; prefer shop and store pages over articles or forums"
)
QUERY_TARGET_GENERAL = "current offers, product listings or availability"


_URL_RE = re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.IGNORECASE)


def _strip_urls(value: Any) -> Any:
    """Remove any URLs a model put in a text field; models fabricate these."""
    if not isinstance(value, str):
        return value
    cleaned = _URL_RE.sub("", value)
    # Tidy leftover brackets/whitespace from removed markdown links.
    cleaned = re.sub(r"[\[\]()]{1,}", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–—:")
    return cleaned or None


def _resolve_link(link_ref: Any, links: list[str]) -> str | None:
    """Map a model-chosen L-number to its real URL. Ignore anything else."""
    if link_ref is None:
        return None
    ref = link_ref
    if isinstance(ref, str):
        match = re.search(r"\d+", ref)
        ref = int(match.group()) if match else None
    if not isinstance(ref, int):
        return None
    idx = ref - 1
    if 0 <= idx < len(links):
        return links[idx]
    return None


def host_of(url: str) -> str:
    """Bare hostname of a URL, without a leading www."""
    netloc = urlparse(url).netloc or url
    return netloc.split("@")[-1].split(":")[0].removeprefix("www.").lower()


def parse_blocklist(text: str | None, defaults: list[str]) -> list[str]:
    """Merge the default blocklist with a user's comma-separated additions."""
    hosts = set(defaults)
    for part in (text or "").split(","):
        part = part.strip().lower().removeprefix("www.")
        if part:
            hosts.add(part)
    return sorted(hosts)


def is_blocked(url: str, blocklist: list[str]) -> bool:
    """True if the URL's host is, or is a subdomain of, a blocked host."""
    host = host_of(url)
    return any(host == b or host.endswith("." + b) for b in blocklist)


def clean_html(
    raw: str, base_url: str = "", max_chars: int = MAX_CONTENT_CHARS
) -> tuple[str, list[str]]:
    """Reduce an HTML document to readable text plus a link catalogue.

    Returns (page_text, links). page_text ends with a LINKS section listing
    up to MAX_LINKS product-ish anchors as "L1 <text> -> <url>", so the model
    can reference a real URL by number instead of writing one. links[i] is the
    absolute URL for reference L(i+1).
    """
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    links = _extract_links(soup, base_url)
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)

    if links:
        catalogue = "\n".join(
            f"L{i + 1} {label} -> {url}" for i, (label, url) in enumerate(links)
        )
        budget = max(0, max_chars - len(catalogue) - 20)
        text = text[:budget] + "\n\nLINKS:\n" + catalogue
    else:
        text = text[:max_chars]
    return text, [url for _, url in links]


def _extract_links(soup: Any, base_url: str) -> list[tuple[str, str]]:
    """Collect distinct, plausible product links as (label, absolute_url)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    base_host = host_of(base_url) if base_url else ""
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href) if base_url else href
        if not url.startswith("http"):
            continue
        # Same-site only: off-site links are rarely the product page.
        if base_host and host_of(url) != base_host:
            continue
        key = url.split("#")[0].rstrip("/")
        if key in seen:
            continue
        label = " ".join(a.get_text(separator=" ").split())[:80]
        if not label:
            continue
        seen.add(key)
        out.append((label, url))
        if len(out) >= MAX_LINKS:
            break
    return out


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


def build_extract_instructions(
    prompt: str, url: str, page_text: str, shopping: bool = False
) -> str:
    """Build the extraction instructions for the AI Task."""
    return EXTRACT_TEMPLATE.format(
        prompt=prompt,
        url=url,
        page_text=page_text,
        shopping_rule=SHOPPING_RULE if shopping else "",
    )


VERIFY_TEMPLATE = (
    "You are verifying one product against a shopper's requirement, using the "
    "text of that product's own page. Answer only from this page; do not use "
    "outside knowledge. Confirm three things: (1) the page really is for a "
    "product matching the requirement, (2) the price, (3) whether it is in "
    "stock and buyable now. Set matches=true only if the product on this page "
    "genuinely fits the requirement. Set in_stock=true only if the page shows "
    "it can be added to basket / bought now; false if it says sold out, out of "
    "stock, unavailable or backorder; leave unset if unclear. Price is a number "
    "with no currency symbol. If the page is not a single product page (e.g. a "
    "category, search or article page), set matches=false.\n\n"
    "Requirement: {prompt}\n\n"
    "Product page URL: {url}\n\n"
    "Product page content:\n{page_text}"
)


def build_verify_instructions(prompt: str, url: str, page_text: str) -> str:
    """Instructions for the per-product verification pass."""
    return VERIFY_TEMPLATE.format(prompt=prompt, url=url, page_text=page_text)


def parse_verification(content: Any) -> dict[str, Any]:
    """Normalise the verification result into matches/name/price/in_stock."""
    data = content
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError) as err:
            raise ValueError(f"Verifier did not return valid JSON: {err}") from err
    if not isinstance(data, dict) or "matches" not in data:
        raise ValueError("Verifier reply is missing the 'matches' field")
    price = data.get("price")
    if isinstance(price, str):
        m = re.search(r"\d+(?:[.,]\d+)?", price)
        price = float(m.group().replace(",", ".")) if m else None
    in_stock = data.get("in_stock")
    if in_stock is not None:
        in_stock = bool(in_stock)
    return {
        "matches": bool(data["matches"]),
        "name": _strip_urls(data.get("name")),
        "price": price,
        "in_stock": in_stock,
    }


def price_agrees(claimed: float | None, verified: float | None, tol: float = 0.15) -> bool:
    """True if the verified price is within tolerance of the claimed one.

    If either price is missing we don't fail on price alone (stock and
    match are the harder gates); a present pair must agree within 15%.
    """
    if claimed is None or verified is None:
        return True
    if verified <= 0:
        return False
    return abs(verified - claimed) / verified <= tol


def build_query_instructions(prompt: str, shopping: bool = False) -> str:
    """Build the query generation instructions for the AI Task."""
    target = QUERY_TARGET_SHOPPING if shopping else QUERY_TARGET_GENERAL
    return QUERY_TEMPLATE.format(prompt=prompt, target=target)


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


def parse_result(content: Any, links: list[str] | None = None) -> dict[str, Any]:
    """Normalise the AI Task extraction result.

    Accepts a dict (the usual case) or a JSON string. If a links catalogue is
    given, an item's link_ref number is resolved to that real URL. Raises
    ValueError if the reply is not usable.
    """
    links = links or []
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
                    "name": _strip_urls(str(entry.get("name"))),
                    "price": price,
                    "availability": _strip_urls(entry.get("availability")),
                    "in_stock": in_stock,
                    "detail": _strip_urls(entry.get("detail")),
                    "link": _resolve_link(entry.get("link_ref"), links),
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
