"""
tests/test_emails.py
Runs the full pipeline (classify → route → forward/queue) against all
mock emails and prints a report. No M365 credentials needed.

Run with:
    python tests/test_emails.py

Expected output: a table showing each email's classification and routing decision.
"""

import logging
import os
import sys
import tempfile

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Suppress info logs during test run for cleaner output
logging.basicConfig(level=logging.WARNING)

from classifier import classify, ClassificationResult
from fallback import FallbackQueue
from router import route
from tests.mock_provider import MockProvider, make_test_emails


def run_tests() -> None:
    emails   = make_test_emails()
    provider = MockProvider(emails)

    # Use a temp file so test runs don't pollute the real fallback queue
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        queue = FallbackQueue(path=tmp.name)

    print("\n" + "=" * 80)
    print("  IT EMAIL ROUTER — PIPELINE TEST")
    print("=" * 80)
    print(f"  Running {len(emails)} test emails through classifier + router")
    print(f"  Gemini model: {os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')}")
    print("=" * 80 + "\n")

    results = []

    for email in provider.fetch_new_emails():
        result: ClassificationResult = classify(email)
        route(email, result, provider, queue)
        results.append((email, result))

    # ── Print results table ───────────────────────────────────────────────────
    print(f"\n{'ID':<12} {'SENDER':<28} {'CATEGORY':<24} {'CONF':>6}  DISPOSITION")
    print("-" * 90)

    for email, result in results:
        import config
        low_conf   = result.confidence < config.CONFIDENCE_THRESHOLD
        is_unknown = result.category == "unknown"
        if low_conf or is_unknown:
            disposition = "→ FALLBACK QUEUE"
        else:
            disposition = f"→ {config.DEPARTMENT_MAP.get(result.category, '?')}"

        print(
            f"{email.id:<12} {email.sender:<28} {result.category:<24} "
            f"{result.confidence:>5.0%}  {disposition}"
        )

    # ── Fallback queue summary ────────────────────────────────────────────────
    pending = queue.list_pending()
    print("\n" + "-" * 90)
    print(f"\nFallback queue: {len(pending)} item(s) awaiting human review")
    for item in pending:
        print(f"  [{item['email_id']}] {item['subject'][:60]}  — {item['reason']}")

    # ── Forwarding log ────────────────────────────────────────────────────────
    sent = provider.get_sent_log()
    print(f"\nForwarded successfully: {len(sent)} email(s)")

    print("\n" + "=" * 80 + "\n")

    # Clean up temp queue file
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


if __name__ == "__main__":
    run_tests()
