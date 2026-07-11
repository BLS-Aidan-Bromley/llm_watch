# LLM Watch

Watch any web page for something you describe in plain English, using a local
LLM (Ollama). No CSS selectors, no scrape configs.

Each watch fetches a URL on a schedule, strips the page to readable text,
and asks your local model one question: does this page contain what the user
asked for? You get:

- `binary_sensor.<watch>_found` — on when it does, with the matched items,
  a summary and prices as attributes
- `sensor.<watch>_matches` — how many matching items were found
- `sensor.<watch>_best_price` — lowest price among the matches
- an `llm_watch_found` event fired the moment a watch flips from
  not-found to found
- an `llm_watch.run_watch` service so automations can trigger checks on demand

## Requirements

- Home Assistant 2024.11 or newer
- An [Ollama](https://ollama.com) server reachable from Home Assistant with a
  model pulled that supports structured output (anything recent: `llama3.2`,
  `llama3.1:8b`, `qwen2.5`, `mistral`)

## Install

1. HACS → three-dot menu → Custom repositories → add `BLS-Aidan-Bromley/llm_watch`,
   category **Integration**
2. Install **LLM Watch**, restart Home Assistant
3. Settings → Devices & services → Add integration → **LLM Watch**

## Adding a watch

You give it a name, a URL, and a description of what you want, e.g.

> an air conditioning unit, portable or fixed, under £300

Pick the page type (auto-detect is fine), point it at your Ollama server,
choose a model and how often to check. Before the watch is created it runs
once against the live page and shows you what it found, so you can tune the
description until the model understands you.

Editing a watch later: Settings → Devices & services → LLM Watch →
Configure.

## JavaScript-heavy sites (Lidl, most supermarkets)

A plain fetch of `lidl.co.uk` returns a near-empty JavaScript shell, so
there is nothing for the model to read. The fix: watch the site's JSON API
instead of the page.

1. Open the site in your browser, open DevTools → Network tab
2. Search for your product on the site
3. Look for the request that returned the results as JSON, copy its URL
4. Use that URL for the watch and set page type to **JSON API**

JSON is also cheaper for the model to read and more stable over time than
scraped HTML. Note Lidl does not publish per-store live stock; you can watch
online availability and the weekly offers, not shelf stock at your local
branch.

## Example automations

Notify when a watch finds something:

```yaml
automation:
  - alias: "Air con spotted at Lidl"
    trigger:
      - platform: event
        event_type: llm_watch_found
        event_data:
          name: "Lidl air con"
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Found: {{ trigger.event.data.name }}"
          message: >-
            {{ trigger.event.data.summary }}
            {% for item in trigger.event.data.items %}
            • {{ item.name }}{% if item.price %} — £{{ item.price }}{% endif %}
            {% endfor %}
```

Run a watch at a specific time instead of (or as well as) its interval:

```yaml
automation:
  - alias: "Morning offer check"
    trigger:
      - platform: time
        at: "07:30:00"
    action:
      - service: llm_watch.run_watch
        data:
          name: "Lidl air con"
```

Omit `name` to run every watch at once.

## How it works

1. Fetch the URL (30s timeout, desktop browser user agent)
2. HTML: strip scripts, styles and boilerplate tags, collapse to plain text,
   truncate to 15k characters. JSON: re-indent and truncate.
3. Send the text to Ollama `/api/chat` with a JSON schema enforced via the
   `format` parameter and temperature 0
4. Parse the reply into `found` / `summary` / `items[{name, price,
   availability, detail}]`
5. Fire the `llm_watch_found` event on a false→true transition

If a check fails (site down, Ollama unreachable, model returned rubbish) the
entities go unavailable and the previous state is kept for the found event
comparison; the error appears in the Home Assistant log.

## Honest limitations

- The model judges the page every run, so results are only as good as the
  model and your description. The pre-create test run is there to tune it.
- Pages rendered entirely client-side cannot be read; use the JSON API route.
- Sites behind aggressive bot protection (Cloudflare challenges) will 403.
- 15k characters of page text means very long listing pages get truncated;
  prefer search-result URLs over category pages.
