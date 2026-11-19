"""Retry/backoff and a circuit breaker for calls to Gemini and BigQuery (requirement 5).

This is deliberately separate from the SQL self-correction loop in the agent graph:
self-correction fixes *logical* SQL failures (a bad query, an empty result) by asking the
model to try again. This module handles *transport* failures (a timeout, a 5xx, a rate
limit) for any external call, Gemini or BigQuery alike -- the two failure classes need
different responses and shouldn't share one retry budget.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class CircuitOpenError(RuntimeError):
    """Raised instead of attempting a call while a circuit breaker is open."""


def _is_transient(exc: BaseException) -> bool:
    """True for errors worth retrying: timeouts, 5xx, rate limits -- not a bad request.

    Broad on purpose at this one boundary: the upstream SDKs (google-genai,
    google-cloud-bigquery) raise their own exception hierarchies, and inspecting the
    message for these markers is more portable than importing every provider's specific
    transient-error classes. A non-transient error (bad request, auth failure, the SQL
    validator rejecting something) is never masked -- it's just not retried, and
    propagates immediately.
    """
    message = str(exc).lower()
    transient_markers = (
        "timeout",
        "timed out",
        "503",
        "502",
        "500",
        "429",
        "unavailable",
        "rate limit",
    )
    return any(marker in message for marker in transient_markers)


class CircuitBreaker:
    """A simple per-service circuit breaker: opens after N consecutive failures.

    Not a library wrapper -- tenacity has no circuit breaker primitive, and this
    prototype's needs are small enough that a hand-rolled one is clearer than pulling in
    a dependency for a dozen lines of logic.
    """

    def __init__(self, *, failure_threshold: int = 5, cooldown_seconds: float = 30.0) -> None:
        """Configure the breaker.

        Args:
            failure_threshold: Consecutive failures before the circuit opens.
            cooldown_seconds: How long the circuit stays open before allowing a retry.
        """
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    def before_call(self, service_name: str) -> None:
        """Raise `CircuitOpenError` if the circuit is open and the cooldown hasn't passed."""
        if self._opened_at is None:
            return
        elapsed = time.monotonic() - self._opened_at
        if elapsed < self._cooldown_seconds:
            raise CircuitOpenError(
                f"{service_name} circuit is open ({self._consecutive_failures} consecutive "
                f"failures); retry in {self._cooldown_seconds - elapsed:.0f}s"
            )
        # Cooldown elapsed: allow one trial call through (half-open).
        self._opened_at = None

    def record_success(self) -> None:
        """Reset the failure count after a successful call."""
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self, service_name: str) -> None:
        """Count a failure, opening the circuit once the threshold is hit."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold and self._opened_at is None:
            self._opened_at = time.monotonic()
            logger.warning(
                "%s circuit opened after %d consecutive failures",
                service_name,
                self._consecutive_failures,
            )


def call_with_resilience[T](
    func: Callable[[], T], *, service_name: str, breaker: CircuitBreaker
) -> T:
    """Call `func`, retrying transient failures with backoff and honoring a circuit breaker.

    Takes a zero-argument callable rather than `func, *args, **kwargs` -- mypy can't type
    a `ParamSpec`'s forwarded args/kwargs alongside extra named parameters like
    `service_name`/`breaker` in between them, and a callers-side `lambda: ...` or
    `functools.partial(...)` is a small enough price for that to be worth avoiding a
    `# type: ignore`.

    Args:
        func: The call to make (a Gemini or BigQuery SDK call, typically), already bound
            to its arguments -- e.g. `lambda: client.query(sql)`.
        service_name: Name used in log messages and `CircuitOpenError`.
        breaker: The circuit breaker guarding this service.

    Returns:
        Whatever `func` returns.

    Raises:
        CircuitOpenError: The breaker is open and the cooldown hasn't elapsed.
        Exception: Whatever `func` raised, once retries are exhausted or the error isn't
            transient.
    """
    breaker.before_call(service_name)

    @retry(
        retry=retry_if_exception(_is_transient),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _attempt() -> T:
        return func()

    try:
        result = _attempt()
    except Exception:
        breaker.record_failure(service_name)
        raise
    breaker.record_success()
    return result
