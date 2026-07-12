"""Constants for the LLM Watch integration."""

DOMAIN = "llm_watch"

# Hub (config entry) fields
CONF_BACKEND = "backend"
CONF_SEARXNG_URL = "searxng_url"
CONF_TAVILY_API_KEY = "tavily_api_key"
CONF_BRAVE_API_KEY = "brave_api_key"
CONF_AI_TASK_ENTITY = "ai_task_entity"

BACKEND_SEARXNG = "searxng"
BACKEND_TAVILY = "tavily"
BACKEND_BRAVE = "brave"
BACKENDS = [BACKEND_TAVILY, BACKEND_SEARXNG, BACKEND_BRAVE]

# Watch (subentry) fields
CONF_NAME = "name"
CONF_URL = "url"
CONF_PROMPT = "prompt"
CONF_MODE = "mode"
CONF_SITES = "sites"
CONF_REQUIRE_IN_STOCK = "require_in_stock"
CONF_SHOPPING_ONLY = "shopping_only"
CONF_BLOCKLIST = "blocklist"
CONF_MAX_PRICE = "max_price"
CONF_SCAN_INTERVAL_HOURS = "scan_interval_hours"
CONF_CREATE_ANYWAY = "create_anyway"

SUBENTRY_PAGE_WATCH = "page_watch"
SUBENTRY_SEARCH_WATCH = "search_watch"

MODE_AUTO = "auto"
MODE_HTML = "html"
MODE_JSON = "json"
MODES = [MODE_AUTO, MODE_HTML, MODE_JSON]

DEFAULT_SCAN_INTERVAL_HOURS = 6

# Cleaned page text is truncated to this many characters before it is
# sent to the model. Keeps prompts small enough for local 8B models.
MAX_CONTENT_CHARS = 15000

# Search watch limits: how many queries the model may propose, and how many
# candidate pages are fetched and judged per run. Every page is one AI Task
# call, so this bounds run time and token use.
MAX_QUERIES = 3
MAX_RESULTS_PER_QUERY = 3
MAX_PAGES = 5

# Most anchor links catalogued from a fetched HTML page and offered to the
# model so it can attribute each item to a real product URL by number.
MAX_LINKS = 40

# Forum, review, roundup and aggregator sites dropped from shopping searches
# before any page is fetched. Editable per watch.
DEFAULT_BLOCKLIST = [
    "reddit.com",
    "quora.com",
    "pinterest.com",
    "pinterest.co.uk",
    "youtube.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "trustpilot.com",
    "which.co.uk",
    "tripadvisor.com",
    "tripadvisor.co.uk",
    "wikipedia.org",
    "medium.com",
    "hotukdeals.com",
    "money.co.uk",
    "moneysavingexpert.com",
]

FETCH_TIMEOUT = 30

EVENT_FOUND = f"{DOMAIN}_found"
EVENT_PRICE_DROP = f"{DOMAIN}_price_drop"
SERVICE_RUN_WATCH = "run_watch"
ATTR_WATCH_NAME = "name"

PLATFORMS = ["binary_sensor", "button", "sensor"]
