"""Simple retry helpers for transient external API failures.

Both helpers follow the same contract:
  - Call the wrapped function once.
  - On failure, check should_retry(exc) — if False, re-raise immediately
    (e.g. auth errors that will never succeed).
  - If retryable and attempts remain, log the attempt and wait with
    exponential backoff (1 s → 2 s → 4 s), then try again.
  - After max_retries exhausted, re-raise the last exception.

Usage:
    # sync (Claude SDK)
    result = retry_sync(lambda: client.messages.create(...),
                        should_retry=_is_retryable)

    # async (httpx)
    result = await retry_async(lambda: client.get(...),
                               should_retry=_is_retryable)
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any, TypeVar

from app.core.logging import logger

T = TypeVar("T")

# Default number of retries (3 retries = 4 total attempts).
MAX_RETRIES = 3


def retry_sync(
    fn: Callable[[], T],
    *,
    max_retries: int = MAX_RETRIES,
    base_delay: float = 1.0,
    should_retry: Callable[[Exception], bool] | None = None,
) -> T:
    """Call fn() up to max_retries+1 times with exponential backoff.

    Args:
        fn:           Zero-argument callable to attempt.
        max_retries:  Maximum number of retries after the first attempt.
        base_delay:   Initial wait in seconds; doubles after each retry.
        should_retry: Optional predicate — return False to stop retrying and
                      re-raise immediately (use for non-transient errors).
    """
    delay = base_delay
    last_exc: Exception

    for attempt in range(1, max_retries + 2):  # attempts: 1 … max_retries+1
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            # Don't retry if the caller says this error is permanent.
            if should_retry is not None and not should_retry(exc):
                raise
            # Don't retry if we've used all attempts.
            if attempt > max_retries:
                break
            logger.warning(
                "Claude API — attempt %d/%d failed: %s. Retrying in %.0fs…",
                attempt,
                max_retries + 1,
                exc,
                delay,
            )
            time.sleep(delay)
            delay *= 2

    raise last_exc


async def retry_async(
    fn: Callable[[], Any],
    *,
    max_retries: int = MAX_RETRIES,
    base_delay: float = 1.0,
    should_retry: Callable[[Exception], bool] | None = None,
) -> Any:
    """Async version of retry_sync — awaits fn() each attempt.

    Args:
        fn:           Zero-argument async callable to attempt.
        max_retries:  Maximum number of retries after the first attempt.
        base_delay:   Initial wait in seconds; doubles after each retry.
        should_retry: Optional predicate — return False to stop retrying and
                      re-raise immediately.
    """
    delay = base_delay
    last_exc: Exception

    for attempt in range(1, max_retries + 2):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            if should_retry is not None and not should_retry(exc):
                raise
            if attempt > max_retries:
                break
            logger.warning(
                "GitHub API — attempt %d/%d failed: %s. Retrying in %.0fs…",
                attempt,
                max_retries + 1,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay *= 2

    raise last_exc
