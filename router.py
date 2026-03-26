"""
router.py
Decides where a classified email should go.

Rules (in order):
  1. Confidence below threshold -> fallback queue (human review)
  2. Category 'unknown'         -> fallback queue
  3. Otherwise                  -> forward to the mapped department address
                                   (with retry on transient failures)
"""

import logging

import alerting
import config
from classifier import ClassificationResult
from dedup import DeduplicationStore
from health import metrics
from providers.base import BaseEmailProvider, StandardEmail
from retry import retry_call

log = logging.getLogger(__name__)


def route(
    email: StandardEmail,
    result: ClassificationResult,
    provider: BaseEmailProvider,
    fallback_queue,
    dedup: DeduplicationStore,
) -> None:
    low_confidence = result.confidence < config.CONFIDENCE_THRESHOLD
    is_unknown     = result.category == "unknown"

    if low_confidence or is_unknown:
        reason = (
            f"low confidence ({result.confidence:.0%})"
            if low_confidence
            else "category unknown"
        )
        log.info("Email %s -> FALLBACK QUEUE (%s) | subject='%s'", email.id, reason, email.subject)
        fallback_queue.enqueue(email, result, reason, provider=provider)
        dedup.mark_processed(email.id, category=result.category, destination="fallback")
        provider.mark_as_processed(email.id)
        metrics.record_fallback()
        return

    destination = config.DEPARTMENT_MAP.get(result.category)
    if not destination:
        log.error("No destination mapped for category '%s' -- sending to fallback.", result.category)
        fallback_queue.enqueue(email, result, "missing destination mapping", provider=provider)
        dedup.mark_processed(email.id, category=result.category, destination="fallback")
        provider.mark_as_processed(email.id)
        metrics.record_fallback()
        return

    log.info(
        "Email %s -> %s [%s, %.0f%%] | subject='%s'",
        email.id, destination, result.category, result.confidence * 100, email.subject,
    )

    try:
        retry_call(provider.forward_email, email, destination,
                   max_attempts=3, base_delay=2.0, label="forward_email")
        dedup.mark_processed(email.id, category=result.category, destination=destination)
        provider.mark_as_processed(email.id)
        metrics.record_routed(result.category)

    except Exception as exc:
        log.error("Forwarding failed for email %s after all retries: %s -- routing to fallback.", email.id, exc)
        alerting.alert_forwarding_failure(email.subject, destination, str(exc))
        fallback_queue.enqueue(email, result, f"forwarding failed: {exc}", provider=provider)
        dedup.mark_processed(email.id, category=result.category, destination="fallback_after_error")
        provider.mark_as_processed(email.id)
        metrics.record_failed()
