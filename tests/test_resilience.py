"""Offline tests for the retry/circuit-breaker helper (requirement 5)."""

from __future__ import annotations

import pytest

from retail_agent.resilience import CircuitBreaker, CircuitOpenError, call_with_resilience


def test_succeeds_on_first_try_without_retrying() -> None:
    calls = []

    def ok() -> str:
        calls.append(1)
        return "fine"

    result = call_with_resilience(ok, service_name="svc", breaker=CircuitBreaker())

    assert result == "fine"
    assert len(calls) == 1


def test_retries_a_transient_failure_then_succeeds() -> None:
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("503 Service Unavailable")
        return "recovered"

    result = call_with_resilience(flaky, service_name="svc", breaker=CircuitBreaker())

    assert result == "recovered"
    assert attempts["n"] == 2


def test_does_not_retry_a_non_transient_failure() -> None:
    attempts = {"n": 0}

    def bad_request() -> str:
        attempts["n"] += 1
        raise ValueError("400 invalid argument")

    with pytest.raises(ValueError, match="invalid argument"):
        call_with_resilience(bad_request, service_name="svc", breaker=CircuitBreaker())

    assert attempts["n"] == 1


def test_circuit_opens_after_repeated_failures_and_blocks_further_calls() -> None:
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0)

    def always_fails() -> str:
        raise RuntimeError("503 unavailable")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            call_with_resilience(always_fails, service_name="svc", breaker=breaker)

    with pytest.raises(CircuitOpenError):
        call_with_resilience(always_fails, service_name="svc", breaker=breaker)


def test_circuit_resets_after_a_success() -> None:
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0)

    def fails_once() -> str:
        raise RuntimeError("503 unavailable")

    with pytest.raises(RuntimeError):
        call_with_resilience(fails_once, service_name="svc", breaker=breaker)

    call_with_resilience(lambda: "ok", service_name="svc", breaker=breaker)

    # Breaker was reset by the success, so it takes a fresh 2 failures to open again.
    with pytest.raises(RuntimeError):
        call_with_resilience(fails_once, service_name="svc", breaker=breaker)
    breaker.before_call("svc")  # does not raise -- still closed
