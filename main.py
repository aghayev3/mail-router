"""
main.py
Entry point — starts the polling loop and wires all components together.
"""

import logging
import logging.handlers
import signal
import sys
import time
from datetime import datetime, timezone

import alerting
import config
from classifier import classify
from dedup import DeduplicationStore
from fallback import FallbackQueue
from health import metrics, start_health_server
from providers.base import BaseEmailProvider
from router import route

# ── Logging with rotation ─────────────────────────────────────────────────────
# RotatingFileHandler caps each log file at 5MB and keeps 5 backups (25MB max).
# Without rotation, a long-running deployment fills the disk.
_file_handler = logging.handlers.RotatingFileHandler(
    "email_router.log",
    maxBytes=5 * 1024 * 1024,   # 5 MB per file
    backupCount=5,               # keep 5 rotated files = 25 MB max on disk
    encoding="utf-8",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        _file_handler,
    ],
)
log = logging.getLogger("main")

# ── Graceful shutdown ─────────────────────────────────────────────────────────
_running = True

def _handle_shutdown(sig, frame):
    global _running
    log.info("Shutdown signal received (%s). Finishing current batch...", signal.Signals(sig).name)
    _running = False

signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT,  _handle_shutdown)


def _build_provider() -> BaseEmailProvider:
    if config.IS_M365:
        from providers.m365 import M365Provider
        return M365Provider()
    else:
        from providers.gmail import GmailProvider
        return GmailProvider()


def run() -> None:
    log.info("=" * 60)
    log.info("IT Email Router starting up")
    log.info("Provider  : %s (polling)", config.EMAIL_PROVIDER.upper())
    log.info("Mailbox   : %s", config.M365_MAILBOX if config.IS_M365 else config.GMAIL_ADDRESS)
    log.info("AI model  : %s", config.GEMINI_MODEL)
    log.info("Poll every: %ss | Confidence threshold: %.0f%%",
             config.POLL_INTERVAL_SECONDS, config.CONFIDENCE_THRESHOLD * 100)
    log.info("=" * 60)

    start_health_server(host=config.HEALTH_BIND, port=config.HEALTH_PORT)

    try:
        provider       = _build_provider()
        fallback_queue = FallbackQueue()
        dedup          = DeduplicationStore(db_path=config.DEDUP_DB_PATH)
    except Exception as exc:
        log.critical("Startup failed: %s", exc, exc_info=True)
        alerting.alert_startup_failure(str(exc))
        sys.exit(1)

    last_prune_date = None

    while _running:
        try:
            emails = provider.fetch_new_emails()
            metrics.record_poll()

            new_emails = [e for e in emails if not dedup.is_processed(e.id)]
            skipped    = len(emails) - len(new_emails)
            if skipped:
                log.debug("Skipped %d already-processed email(s).", skipped)

            if not new_emails:
                log.debug("No new emails. Next poll in %ss.", config.POLL_INTERVAL_SECONDS)
            else:
                log.info("Fetched %d new email(s). Processing...", len(new_emails))
                for email in new_emails:
                    try:
                        result = classify(email)
                        route(email, result, provider, fallback_queue, dedup)
                    except Exception as exc:
                        log.error("Unhandled error processing email %s: %s", email.id, exc, exc_info=True)
                        metrics.record_failed()

            queue_depth = len(fallback_queue.list_pending())
            metrics.set_fallback_queue_depth(queue_depth)
            if queue_depth >= config.FALLBACK_ALERT_THRESHOLD:
                alerting.alert_fallback_queue_spike(queue_depth, config.FALLBACK_ALERT_THRESHOLD)

            metrics.record_poll_success()

            today = datetime.now(timezone.utc).date()
            if last_prune_date != today:
                dedup.prune(keep_days=config.DEDUP_PRUNE_DAYS)
                last_prune_date = today

        except Exception as exc:
            metrics.record_poll_error(str(exc))
            log.error("Poll cycle error: %s", exc, exc_info=True)
            snap = metrics.snapshot()
            if snap["consecutive_errors"] >= config.ALERT_CONSECUTIVE_FAILURES:
                alerting.alert_consecutive_failures(
                    snap["consecutive_errors"],
                    snap.get("last_error_message", str(exc)),
                )

        elapsed = 0
        while _running and elapsed < config.POLL_INTERVAL_SECONDS:
            time.sleep(1)
            elapsed += 1

    log.info("Email Router shut down cleanly.")


if __name__ == "__main__":
    run()
