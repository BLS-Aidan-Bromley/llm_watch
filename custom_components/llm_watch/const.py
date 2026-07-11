"""Constants for the LLM Watch integration."""

DOMAIN = "llm_watch"

CONF_NAME = "name"
CONF_URL = "url"
CONF_PROMPT = "prompt"
CONF_MODE = "mode"
CONF_OLLAMA_URL = "ollama_url"
CONF_MODEL = "model"
CONF_SCAN_INTERVAL_HOURS = "scan_interval_hours"
CONF_CREATE_ANYWAY = "create_anyway"

MODE_AUTO = "auto"
MODE_HTML = "html"
MODE_JSON = "json"
MODES = [MODE_AUTO, MODE_HTML, MODE_JSON]

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2"
DEFAULT_SCAN_INTERVAL_HOURS = 6

# Cleaned page text is truncated to this many characters before it is
# sent to the model. Keeps prompts small enough for local 8B models.
MAX_CONTENT_CHARS = 15000

FETCH_TIMEOUT = 30
OLLAMA_TIMEOUT = 180

EVENT_FOUND = f"{DOMAIN}_found"
SERVICE_RUN_WATCH = "run_watch"
ATTR_WATCH_NAME = "name"

PLATFORMS = ["binary_sensor", "sensor"]
