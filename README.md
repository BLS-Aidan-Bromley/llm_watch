# LLM Watch

Tell Home Assistant what you're after, in plain English, and it hunts the web
for it on a schedule: deals, promotions, specific products, stock. Uses
whatever AI you already have in Home Assistant (via AI Tasks) and, for web
searches, your own SearXNG instance. No CSS selectors, no scrape configs.

## Two kinds of watch

**Page watch** — watches one URL. "Is there a portable air conditioning unit
on this page?"

**Web search watch** — no URL. This runs a deliberately thorough two-pass
hunt, not a quick scan:

1. *Discovery.* The AI writes search queries, the backend finds candidate
   pages, and items are gathered. Shopify retailers are read from their own
   `products.json` (exact prices, stock and product links); other pages are
   read by the AI.
2. *Verification.* Every candidate is then visited on its own product page
   and independently confirmed: is this really the product, does the price
   hold, is it in stock? Anything that fails, or can't be read, is dropped,
   never suggested. Shopify products are confirmed from the store's JSON;
   WooCommerce products from the Store API; others are re-read and checked
   by the AI, and only when the item has its own product page. A candidate
   whose only link is a shared listing page is dropped as unverifiable,
   because that is how names and prices get crossed.

If a round verifies nothing, it searches again with fresh queries, up to the
max-rounds limit (default 3), then reports only what passed, or nothing.
A run takes minutes, by design. Optional criteria: shopping-results-only
(drops forums, reviews and roundups), in stock only, and a maximum price.
Turn verification off for a fast, unchecked scan.

Every watch gives you:

- `binary_sensor.<watch>_found` — on when the criteria are met, with items,
  prices, sources, and a summary as attributes
- `sensor.<watch>_matches` and `sensor.<watch>_best_price`
- `llm_watch_found` event when a watch flips from not-found to found
- `llm_watch_price_drop` event when the best price falls between checks
  (with old_price and new_price)
- `llm_watch.run_watch` service to trigger checks from automations
- a **Check now** button on every watch

## Requirements

- Home Assistant 2025.9 or newer
- Any AI integration with an **AI Task** entity (Ollama, OpenAI, Anthropic,
  Google, OpenRouter, ...). Add the "AI Task" sub-entry in your AI
  integration if you haven't already.
- For web search watches, one search backend, chosen at hub setup:

**Tavily** (recommended). Sign up free at [tavily.com](https://tavily.com),
copy the API key (`tvly-...`). 1,000 free searches a month, no card needed;
a watch run uses up to 3. Tavily also extracts page content in the search
itself, so these watches skip page fetching entirely, which avoids retailer
bot-blocking and is faster.

**SearXNG** (fully local, unlimited). Self-hosted; enable the JSON format
in `settings.yml`:

```yaml
search:
  formats:
    - html
    - json
```

**Brave Search API**. Get a key at
[brave.com/search/api](https://brave.com/search/api) (free tier available).
Independent index; returns results only, so pages are fetched separately.

## Install

1. HACS → Custom repositories → add `BLS-Aidan-Bromley/llm_watch`,
   category **Integration**
2. Install, restart Home Assistant
3. Settings → Devices & services → Add integration → **LLM Watch**
4. Hub setup: pick a search backend, enter its API key or URL, and
   optionally set a default AI Task entity
5. On the integration page, use **Add page watch** / **Add web search watch**

Each watch runs a live test before it's created and shows you what it found,
so you can tune the description. A web search test run makes several AI calls
and can take a few minutes on a local model.

## Example automations

```yaml
automation:
  - alias: "Air con found in stock"
    trigger:
      - platform: event
        event_type: llm_watch_found
        event_data:
          name: "Air con hunt"
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Found: {{ trigger.event.data.name }}"
          message: >-
            {{ trigger.event.data.summary }}
            {% for item in trigger.event.data.items %}
            • {{ item.name }}{% if item.price %} — £{{ item.price }}{% endif %} {{ item.link or item.source }}
            {% endfor %}

  - alias: "Price dropped"
    trigger:
      - platform: event
        event_type: llm_watch_price_drop
    action:
      - service: notify.mobile_app_your_phone
        data:
          message: >-
            {{ trigger.event.data.name }}: price down from
            £{{ trigger.event.data.old_price }} to £{{ trigger.event.data.new_price }}
```

Run a watch at a set time: call `llm_watch.run_watch` with the watch name
(or no name for all watches) from any automation.

## Upgrading from 0.2

The 0.2 single-watch entry is migrated automatically into the new hub layout
with your watch attached as a page watch. Pick a search backend by
reconfiguring the hub if you want search watches.

## Honest limitations

- Results are judgements by your model on page text; the pre-create test run
  is there to tune the description. Smaller local models are more literal.
- Links shown for each item are the real page the content came from. The
  model is barred from writing URLs (it fabricates them), so if a page has no
  clean source you get the item without a working link rather than a fake one.
- Shopping-results-only filters twice: a built-in blocklist (Reddit, YouTube,
  Trustpilot and similar, editable per watch) drops junk before fetching, and
  the model is told to reject forum, review and roundup pages. A small model
  will still occasionally misjudge a borderline page.
- JavaScript-only pages can't be read. Page watches should use the site's
  JSON API for those; search watches skip unreadable pages automatically.
- Sites behind aggressive bot protection will fail to fetch.
- Per-store stock only works where the retailer publishes it; restrict a
  search watch to those retailers' sites for reliable stock checks.
- Price-drop comparison is against the previous check and survives Home
  Assistant restarts (the baseline is persisted to storage).
- Search watches make 1 + (pages checked, max 5) AI calls per run. On a
  local 8B model expect a run to take a minute or two; schedule accordingly.
