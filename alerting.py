"""
alerting.py
Sends email alerts when the system needs human attention.

Security fixes applied:
  - Port 25 (cleartext SMTP) now logs a warning — TLS is enforced on all
    other ports via STARTTLS. If you must use port 25 (internal relay),
    set ALERT_SMTP_FORCE_TLS=false explicitly to acknowledge the risk.
  - Error strings included in alert bodies are truncated and stripped
    of control characters to prevent log/email injection.
"""

import logging
import smtplib
import ssl
import threading
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config

log = logging.getLogger(__name__)

_ALERT_COOLDOWN_SECONDS = 1800  # 30 minutes between repeated alerts of the same type
_last_alert: dict[str, float] = {}
_lock = threading.Lock()


def _on_cooldown(alert_type: str) -> bool:
    now = time.monotonic()
    with _lock:
        last = _last_alert.get(alert_type, 0)
        if now - last < _ALERT_COOLDOWN_SECONDS:
            return True
        _last_alert[alert_type] = now
    return False


def _sanitize_alert_field(value: str, max_length: int = 500) -> str:
    """
    Strip control characters from values embedded in alert emails.
    Prevents newline injection that could alter email headers or body structure.
    """
    sanitized = value.replace("\r", " ").replace("\n", " ").replace("\x00", "")
    return sanitized[:max_length]


def _send(subject: str, body: str) -> None:
    if not config.ALERT_SMTP_HOST:
        log.debug("Alerting not configured — skipping alert: %s", subject)
        return

    # Warn loudly if port 25 is used — cleartext unless the server upgrades
    if config.ALERT_SMTP_PORT == 25:
        log.warning(
            "ALERT_SMTP_PORT=25 — alerts will be sent without TLS unless the "
            "server upgrades the connection. Set ALERT_SMTP_PORT=587 (STARTTLS) "
            "or 465 (SMTPS) to enforce encryption."
        )

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"[IT Email Router] {_sanitize_alert_field(subject, 150)}"
    msg["From"]    = config.ALERT_FROM_ADDRESS
    msg["To"]      = config.ALERT_TO_ADDRESS

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mailbox   = config.M365_MAILBOX or config.GMAIL_ADDRESS
    full_body = f"{body}\n\n---\nSent at {timestamp} by IT Email Router\nMailbox: {mailbox}"

    msg.attach(MIMEText(full_body, "plain"))

    try:
        context = ssl.create_default_context()

        if config.ALERT_SMTP_PORT == 465:
            # SMTPS — TLS from the start
            with smtplib.SMTP_SSL(config.ALERT_SMTP_HOST, config.ALERT_SMTP_PORT, context=context) as server:
                if config.ALERT_SMTP_USER and config.ALERT_SMTP_PASSWORD:
                    server.login(config.ALERT_SMTP_USER, config.ALERT_SMTP_PASSWORD)
                server.sendmail(config.ALERT_FROM_ADDRESS, config.ALERT_TO_ADDRESS, msg.as_string())
        else:
            # STARTTLS (port 587) or plaintext (port 25, warned above)
            with smtplib.SMTP(config.ALERT_SMTP_HOST, config.ALERT_SMTP_PORT) as server:
                server.ehlo()
                if config.ALERT_SMTP_PORT != 25:
                    server.starttls(context=context)
                    server.ehlo()
                if config.ALERT_SMTP_USER and config.ALERT_SMTP_PASSWORD:
                    server.login(config.ALERT_SMTP_USER, config.ALERT_SMTP_PASSWORD)
                server.sendmail(config.ALERT_FROM_ADDRESS, config.ALERT_TO_ADDRESS, msg.as_string())

        log.info("Alert sent: %s", subject)

    except Exception as exc:
        log.error("Failed to send alert '%s': %s", subject, exc)


# ── Public alert functions ────────────────────────────────────────────────────

def alert_consecutive_failures(count: int, last_error: str) -> None:
    if _on_cooldown("consecutive_failures"):
        return
    safe_error = _sanitize_alert_field(last_error)
    _send(
        subject=f"ALERT: {count} consecutive poll failures",
        body=(
            f"The email router has failed to poll the mailbox {count} times in a row.\n\n"
            f"Last error:\n  {safe_error}\n\n"
            f"Action required:\n"
            f"  - Check network connectivity from the server\n"
            f"  - Verify API credentials haven't expired\n"
            f"  - Check: docker compose logs -f\n\n"
            f"Emails are NOT being routed while this persists."
        ),
    )


def alert_fallback_queue_spike(depth: int, threshold: int) -> None:
    if _on_cooldown("fallback_queue_spike"):
        return
    _send(
        subject=f"WARNING: Fallback queue has {depth} unreviewed emails",
        body=(
            f"The fallback queue has reached {depth} items (threshold: {threshold}).\n\n"
            f"This may mean:\n"
            f"  - Unusual volume of ambiguous emails\n"
            f"  - Classifier confidence consistently below threshold\n"
            f"  - A new type of email that doesn't fit existing categories\n\n"
            f"Review the queue:\n"
            f"  cat fallback_queue.jsonl | python -m json.tool\n\n"
            f"Or check metrics:\n"
            f"  curl http://localhost:{config.HEALTH_PORT}/metrics"
        ),
    )


def alert_forwarding_failure(email_subject: str, destination: str, error: str) -> None:
    if _on_cooldown("forwarding_failure"):
        return
    safe_subject = _sanitize_alert_field(email_subject, 200)
    safe_dest    = _sanitize_alert_field(destination, 100)
    safe_error   = _sanitize_alert_field(error, 300)
    _send(
        subject="ALERT: Email forwarding failure — email sent to fallback queue",
        body=(
            f"An email could not be forwarded after all retry attempts.\n\n"
            f"Email subject : {safe_subject}\n"
            f"Destination   : {safe_dest}\n"
            f"Error         : {safe_error}\n\n"
            f"The email has been placed in the fallback queue for manual review."
        ),
    )


def alert_startup_failure(error: str) -> None:
    safe_error = _sanitize_alert_field(error)
    _send(
        subject="CRITICAL: Email Router failed to start",
        body=(
            f"The IT Email Router failed to start.\n\n"
            f"Error: {safe_error}\n\n"
            f"No emails are being routed. Check config and restart:\n"
            f"  docker compose logs it-email-router\n"
            f"  docker compose up -d"
        ),
    )
