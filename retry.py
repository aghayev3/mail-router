"""
retry.py
Retry decorator with exponential backoff.

Used to wrap email forwarding calls so transient failures
(network blip, API rate limit, temporary M365/Gmail outage)
are retried before giving up and routing to the fallback queue.

Backoff schedule (default):
  Attempt 1: immediate
  Attempt 2: wait 2s
  Attempt 3: wait 4s
  → give up, raise last exception

All retry attempts and waits are logged so failures are visible
in both the log file and the metrics endpoint.
"""

import logging
import time
from functools import wraps
from typing import Callable, Tuple, Type

log = logging.getLogger(__name__)


def with_retry(
    max_attempts: int = 3,
    base_delay:   float = 2.0,
    backoff:      float = 2.0,
    exceptions:   Tuple[Type[Exception], ...] = (Exception,),
):
    """
    Decorator factory. Wraps a function with retry + exponential backoff.

    Args:
        max_attempts: total number of tries (including the first)
        base_delay:   seconds to wait before the second attempt
        backoff:      multiplier applied to delay on each subsequent attempt
        exceptions:   tuple of exception types that trigger a retry
                      (exceptions NOT in this tuple propagate immediately)

    Usage:
        @with_retry(max_attempts=3, exceptions=(requests.HTTPError,))
        def forward_email(...):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        log.error(
                            "%s failed after %d attempt(s): %s",
                            fn.__qualname__, attempt, exc,
                        )
                        break
                    log.warning(
                        "%s attempt %d/%d failed: %s — retrying in %.1fs",
                        fn.__qualname__, attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)
                    delay *= backoff

            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator


def retry_call(
    fn: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    label: str = "",
    **kwargs,
):
    """
    Inline retry helper (no decorator needed).

    Usage:
        retry_call(provider.forward_email, email, destination, label="forward")
    """
    delay = base_delay
    last_exc: Exception | None = None
    name = label or fn.__qualname__

    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt == max_attempts:
                log.error("%s failed after %d attempt(s): %s", name, attempt, exc)
                break
            log.warning(
                "%s attempt %d/%d failed: %s — retrying in %.1fs",
                name, attempt, max_attempts, exc, delay,
            )
            time.sleep(delay)
            delay *= 2.0

    raise last_exc  # type: ignore[misc]
