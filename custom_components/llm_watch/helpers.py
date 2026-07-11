"""Pure helpers for LLM Watch.

Everything in this module is deliberately free of Home Assistant imports
so it can be unit tested standalone.
"""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from .const import MAX_CONTENT_CHARS

# Tags that never contain content worth sending to the model.
_STRIP_TAGS = ["script", "style", "noscript", "svg", "iframe", "head", "template"]

# Ollama structured-output schema. The model must return exactly this shape.
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "summary": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "price": {"type": ["number", "null"]},
                    "availability": {"type": ["string", "null"]},
                    "detail": {"type": ["string", "null"]},
                },
                "required": ["name"],
            },
        },
    },
    "required": ["found", "summary", "items"],
}

SYSTEM_PROMPT = (
    "You are a data extraction assistant inside a home automation system. "
    "You are given the text content of a web page and a description of what "
    "the user is looking for. Decide whether the page contains what they "
    "want. Only report items that genuinely match the description; do not "
    "pad the list with loosely related products. Prices must be numbers "
    "without currency symbols. If nothing matches, return found=false with "
    "an empty items list and a one-sentence summary of what the page showed "
    "instead. Never invent items that are not on the page."
)


def clean_html(raw: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Reduce an HTML document to readable text for the model."""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse whitespace: strip each line, drop blanks, cap repeats.
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


def build_messages(prompt: str, url: str, page_text: str) -> list[dict[str, str]]:
    """Build the chat messages for the Ollama request."""
    user = (
        f"The user is looking for: {prompt}\n\n"
        f"Page URL: {url}\n\n"
        f"Page content:\n{page_text}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_result(content: str) -> dict[str, Any]:
    """Parse and normalise the model's JSON reply.

    Raises ValueError if the reply is not usable.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError) as err:
        raise ValueError(f"Model did not return valid JSON: {err}") from err
    if not isinstance(data, dict) or "found" not in data:
        raise ValueError("Model reply is missing the 'found' field")

    items_in = data.get("items") or []
    items: list[dict[str, Any]] = []
    if isinstance(items_in, list):
        for entry in items_in:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            price = entry.get("price")
            if isinstance(price, str):
                # Models occasionally return "149.99" or "£149.99" despite the schema.
                match = re.search(r"\d+(?:[.,]\d+)?", price)
                price = float(match.group().replace(",", ".")) if match else None
            items.append(
                {
                    "name": str(entry.get("name")),
                    "price": price,
                    "availability": entry.get("availability"),
                    "detail": entry.get("detail"),
                }
            )

    return {
        "found": bool(data["found"]),
        "summary": str(data.get("summary") or ""),
        "items": items,
    }
