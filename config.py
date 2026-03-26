"""
config.py
Loads all configuration from environment variables.
Validates ranges and required fields at startup so the app fails fast
with a clear message rather than misbehaving silently later.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Check your .env file against .env.example."
        )
    return value


def _require_if(condition: bool, key: str) -> str:
    if condition:
        return _require(key)
    return os.getenv(key, "")


def _int_range(key: str, default: int, min_val: int, max_val: int) -> int:
    """Parse an integer env var and enforce a valid range."""
    raw = os.getenv(key, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise EnvironmentError(f"'{key}' must be an integer, got: {raw!r}")
    if not (min_val <= value <= max_val):
        raise EnvironmentError(
            f"'{key}' must be between {min_val} and {max_val}, got: {value}"
        )
    return value


def _float_range(key: str, default: float, min_val: float, max_val: float) -> float:
    """Parse a float env var and enforce a valid range."""
    raw = os.getenv(key, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise EnvironmentError(f"'{key}' must be a number, got: {raw!r}")
    if not (min_val <= value <= max_val):
        raise EnvironmentError(
            f"'{key}' must be between {min_val} and {max_val}, got: {value}"
        )
    return value


# ── Email provider ────────────────────────────────────────────────────────────
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "m365").lower()
if EMAIL_PROVIDER not in ("m365", "gmail"):
    raise EnvironmentError("EMAIL_PROVIDER must be 'm365' or 'gmail'.")

IS_M365  = EMAIL_PROVIDER == "m365"
IS_GMAIL = EMAIL_PROVIDER == "gmail"

# ── Gemini ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = _require("GEMINI_API_KEY")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")

# ── Microsoft 365 ─────────────────────────────────────────────────────────────
M365_TENANT_ID     = _require_if(IS_M365, "M365_TENANT_ID")
M365_CLIENT_ID     = _require_if(IS_M365, "M365_CLIENT_ID")
M365_CLIENT_SECRET = _require_if(IS_M365, "M365_CLIENT_SECRET")
M365_MAILBOX       = _require_if(IS_M365, "M365_MAILBOX")

# ── Gmail ─────────────────────────────────────────────────────────────────────
GMAIL_CLIENT_ID     = _require_if(IS_GMAIL, "GMAIL_CLIENT_ID")
GMAIL_CLIENT_SECRET = _require_if(IS_GMAIL, "GMAIL_CLIENT_SECRET")
GMAIL_REFRESH_TOKEN = _require_if(IS_GMAIL, "GMAIL_REFRESH_TOKEN")
GMAIL_ADDRESS       = _require_if(IS_GMAIL, "GMAIL_ADDRESS")

# ── Polling ───────────────────────────────────────────────────────────────────
# Min 10s to avoid hammering the API; max 1 hour as a sanity cap
POLL_INTERVAL_SECONDS = _int_range("POLL_INTERVAL_SECONDS", default=60, min_val=10, max_val=3600)

# ── Routing ───────────────────────────────────────────────────────────────────
DEPARTMENT_MAP: dict[str, str] = {
    "help_desk":            _require("EMAIL_HELP_DESK"),
    "networking":           _require("EMAIL_NETWORKING"),
    "cybersecurity":        _require("EMAIL_CYBERSECURITY"),
    "system_administrator": _require("EMAIL_SYSADMIN"),
    "unknown":              _require("EMAIL_FALLBACK"),
}

# ── Classification ────────────────────────────────────────────────────────────
# Must be between 0.0 and 1.0 — values outside this range break routing logic
CONFIDENCE_THRESHOLD = _float_range("CONFIDENCE_THRESHOLD", default=0.70, min_val=0.0, max_val=1.0)

# ── Fallback queue ────────────────────────────────────────────────────────────
FALLBACK_QUEUE_PATH      = os.getenv("FALLBACK_QUEUE_PATH", "fallback_queue.jsonl")
FALLBACK_ALERT_THRESHOLD = _int_range("FALLBACK_ALERT_THRESHOLD", default=10, min_val=1, max_val=10000)

# ── Deduplication ─────────────────────────────────────────────────────────────
DEDUP_DB_PATH    = os.getenv("DEDUP_DB_PATH", "processed_emails.db")
DEDUP_PRUNE_DAYS = _int_range("DEDUP_PRUNE_DAYS", default=90, min_val=1, max_val=3650)

# ── Health server ─────────────────────────────────────────────────────────────
HEALTH_PORT = _int_range("HEALTH_PORT", default=8080, min_val=1024, max_val=65535)

# Bind to localhost by default — only expose externally if you intentionally
# set HEALTH_BIND=0.0.0.0 (e.g. for a monitoring system on another host)
HEALTH_BIND = os.getenv("HEALTH_BIND", "127.0.0.1")

# ── Alerting ──────────────────────────────────────────────────────────────────
ALERT_SMTP_HOST     = os.getenv("ALERT_SMTP_HOST", "")
ALERT_SMTP_PORT     = _int_range("ALERT_SMTP_PORT", default=587, min_val=1, max_val=65535)
ALERT_SMTP_USER     = os.getenv("ALERT_SMTP_USER", "")
ALERT_SMTP_PASSWORD = os.getenv("ALERT_SMTP_PASSWORD", "")
ALERT_FROM_ADDRESS  = os.getenv("ALERT_FROM_ADDRESS", "")
ALERT_TO_ADDRESS    = os.getenv("ALERT_TO_ADDRESS", "")
ALERT_CONSECUTIVE_FAILURES = _int_range("ALERT_CONSECUTIVE_FAILURES", default=5, min_val=1, max_val=100)
