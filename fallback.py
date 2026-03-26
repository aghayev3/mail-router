"""
fallback.py
Handles emails that couldn't be classified with sufficient confidence.

Strategy: forward the email to a human-review inbox with a clear
context header showing the AI's best guess and why it wasn't confident.
The reviewer reads it, decides, and manually forwards to the right team.
No technical tools required — it's just a normal email inbox.

The .jsonl audit log is kept as a secondary record for metrics and
pattern analysis, but the primary mechanism is the forwarded email.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import config
from classifier import ClassificationResult
from providers.base import BaseEmailProvider, StandardEmail

log = logging.getLogger(__name__)

# Category labels shown to the human reviewer — plain English, not code keys
_CATEGORY_LABELS = {
    "help_desk":            "Help Desk (password resets, software, printer issues)",
    "networking":           "Networking (VPN, Wi-Fi, connectivity)",
    "cybersecurity":        "Cybersecurity (phishing, suspicious activity, malware)",
    "system_administrator": "System Administrator (servers, AD, backups, infrastructure)",
    "unknown":              "Unknown — AI could not determine a category",
}


class FallbackQueue:

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path or config.FALLBACK_QUEUE_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)

    def enqueue(
        self,
        email: StandardEmail,
        result: ClassificationResult,
        reason: str,
        provider: BaseEmailProvider | None = None,
    ) -> None:
        """
        Handle a low-confidence or unclassifiable email.

        1. Forward it to the human-review inbox with a context header
           so the reviewer knows exactly what the AI thought and why.
        2. Write an audit record to the .jsonl log.

        If forwarding fails, the audit log still gets written so the
        email isn't silently lost — an admin can retrieve it by email_id.
        """
        destination = config.DEPARTMENT_MAP.get("unknown", "")

        # ── Forward to human reviewer ─────────────────────────────────────────
        if provider and destination:
            self._forward_for_review(email, result, reason, provider, destination)
        else:
            log.warning(
                "No provider or fallback address configured — "
                "email %s logged to audit file only.", email.id
            )

        # ── Audit log ─────────────────────────────────────────────────────────
        self._write_audit(email, result, reason)

    def _forward_for_review(
        self,
        email: StandardEmail,
        result: ClassificationResult,
        reason: str,
        provider: BaseEmailProvider,
        destination: str,
    ) -> None:
        """
        Forward the email to the fallback inbox with a human-readable
        context block prepended so the reviewer has all the information
        they need to make a routing decision without any technical tools.
        """
        category_label = _CATEGORY_LABELS.get(result.category, result.category)
        confidence_pct = f"{result.confidence:.0%}"
        threshold_pct  = f"{config.CONFIDENCE_THRESHOLD:.0%}"

        # Build a clear, non-technical context header for the reviewer
        context_header = (
            f"{'=' * 60}\n"
            f"  ACTION REQUIRED — Manual Email Routing\n"
            f"{'=' * 60}\n"
            f"\n"
            f"The IT Email Router could not automatically route this\n"
            f"email. Please read it and forward it to the right team.\n"
            f"\n"
            f"  AI best guess  : {category_label}\n"
            f"  Confidence     : {confidence_pct}  (minimum required: {threshold_pct})\n"
            f"  Reason         : {reason}\n"
            f"  AI reasoning   : {result.reasoning}\n"
            f"\n"
            f"  Original sender  : {email.sender}\n"
            f"  Received at      : {email.timestamp.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"\n"
            f"Department email addresses:\n"
        )

        # Add routing targets so the reviewer can forward with one click
        for key, address in config.DEPARTMENT_MAP.items():
            if key == "unknown":
                continue
            label = _CATEGORY_LABELS.get(key, key).split(" (")[0]
            context_header += f"  {label:<25} {address}\n"

        context_header += f"\n{'=' * 60}\n\n--- Original message ---\n\n"

        try:
            provider.forward_for_review(email, destination, context_header)
            log.info(
                "Email %s → FALLBACK INBOX %s (%s, %s) | subject='%s'",
                email.id, destination, result.category, confidence_pct, email.subject,
            )
        except Exception as exc:
            log.error(
                "Failed to forward email %s to fallback inbox: %s — "
                "email logged to audit file only.", email.id, exc,
            )

    def _write_audit(
        self,
        email: StandardEmail,
        result: ClassificationResult,
        reason: str,
    ) -> None:
        """Write a metadata record to the audit log."""
        record = {
            "queued_at":  datetime.now(timezone.utc).isoformat(),
            "reason":     reason,
            "email_id":   email.id,
            "sender":     email.sender,
            "subject":    email.subject,
            "timestamp":  email.timestamp.isoformat(),
            "category":   result.category,
            "confidence": round(result.confidence, 4),
            "reasoning":  result.reasoning,
            # Body intentionally omitted — retrieve via email_id from provider if needed
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.error("Failed to write fallback audit record: %s", exc)

    def list_pending(self) -> list[dict]:
        """Return all audit records — used by metrics and health reporting."""
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
            return [json.loads(line) for line in lines if line.strip()]
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Failed to read fallback audit log: %s", exc)
            return []

    def clear(self) -> None:
        """Clear the audit log (call after reviewing all items)."""
        self._path.write_text("", encoding="utf-8")
        log.info("Fallback audit log cleared.")
