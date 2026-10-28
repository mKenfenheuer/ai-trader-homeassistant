"""Constants of the AI Trader integration."""

from datetime import timedelta

DOMAIN = "ai_trader"
MANUFACTURER = "AI Trader"

CONF_VERIFY_SSL = "verify_ssl"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 15

# Calendars ask for their window on every view; the events change rarely.
EVENT_CACHE_TTL = timedelta(minutes=5)

EVENT_SIGNAL_PENDING = f"{DOMAIN}_signal_pending"
EVENT_SIGNAL_CLOSED = f"{DOMAIN}_signal_closed"
EVENT_RUN_FINISHED = f"{DOMAIN}_run_finished"

SERVICE_APPROVE = "approve_signal"
SERVICE_REJECT = "reject_signal"
SERVICE_RUN = "run"
SERVICE_IMPORT_HISTORY = "import_history"

ATTR_SIGNAL_ID = "signal_id"
ATTR_PORTFOLIO_ID = "portfolio_id"
ATTR_ENGINE = "engine"
ATTR_INSTRUCTION = "instruction"
ATTR_CONFIG_ENTRY = "config_entry_id"
