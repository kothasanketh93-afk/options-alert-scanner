"""
production/retry_manager.py
----------------------------
Part 9, item 3: Retry Framework.

Relationship to the existing scripts/retry_utils.py
-----------------------------------------------------
retry_utils.request_with_retry() is untouched and keeps handling every
existing HTTP call (Angel One, Upstox, Telegram) exactly as before --
including its carefully-tuned 401/403/429/empty-body-400 handling,
which is trading-critical behavior this task must not change.

This module adds what retry_utils.py doesn't cover, without touching
it:

1. A generic `@retry()` decorator / `RetryManager.call()` for
   NON-HTTP retryable operations the spec calls out that retry_utils
   was never meant for: config loading, replay loading, WebSocket
   reconnect, or any other function call.
2. Circuit breakers: after too many consecutive failures against a
   given name (e.g. "angelone", "telegram", "websocket"), stop
   retrying for a cooldown period and fail fast instead -- so a
   broker outage doesn't turn every scan cycle into a multi-minute
   pile of doomed retries.

Existing call sites do not need to change. New/updated call sites, or
a future migration of retry_utils' callers, can opt in via
`retry_manager.call(...)` or the `@retry(...)` decorator.
"""

from __future__ import annotations

import functools
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Optional, TypeVar

from exceptions import CircuitBreakerOpenError, RetryExhaustedError

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """One policy object instead of scattering max_attempts/backoff/
    jitter args through every call site."""

    max_attempts: int = 3
    base_backoff: float = 1.5
    max_backoff: float = 60.0
    jitter_fraction: float = 0.25
    timeout: Optional[float] = None  # per-attempt wall-clock budget, seconds
    retryable_exceptions: tuple = (Exception,)


class CircuitState(Enum):
    CLOSED = "closed"      # normal operation
    OPEN = "open"          # failing fast, not attempting calls
    HALF_OPEN = "half_open"  # cooldown elapsed, letting one trial call through


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5       # consecutive failures before opening
    cooldown_seconds: float = 30.0   # how long to stay open before a trial call
    success_threshold: int = 2       # consecutive half-open successes to fully close


class CircuitBreaker:
    """Simple consecutive-failure circuit breaker, keyed by name so the
    same breaker instance is shared by every call site for a given
    dependency (e.g. all Angel One calls share the "angelone" breaker)."""

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._opened_at: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state is CircuitState.OPEN and self._opened_at is not None:
                if time.monotonic() - self._opened_at >= self.config.cooldown_seconds:
                    self._state = CircuitState.HALF_OPEN
            return self._state

    def before_call(self) -> None:
        state = self.state
        if state is CircuitState.OPEN:
            remaining = self.config.cooldown_seconds - (time.monotonic() - (self._opened_at or 0))
            raise CircuitBreakerOpenError(
                f"circuit '{self.name}' is open ({self._consecutive_failures} consecutive "
                f"failures) -- refusing call, retry after {max(0.0, remaining):.1f}s",
                breaker_name=self.name,
                retry_after=max(0.0, remaining),
            )

    def record_success(self) -> None:
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._consecutive_successes = 0
                    self._opened_at = None
            else:
                self._consecutive_failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self._consecutive_successes = 0
            if self._state is CircuitState.HALF_OPEN or (
                self._state is CircuitState.CLOSED
                and self._consecutive_failures >= self.config.failure_threshold
            ):
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()


class RetryManager:
    """Owns named circuit breakers and executes calls under a
    RetryPolicy. One process-wide instance (`retry_manager`, below) is
    normally enough; construct your own if you need isolated breaker
    state (e.g. in tests)."""

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get_breaker(self, name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config)
            return self._breakers[name]

    def call(
        self,
        fn: Callable[..., T],
        *args,
        name: str,
        policy: Optional[RetryPolicy] = None,
        breaker_config: Optional[CircuitBreakerConfig] = None,
        use_circuit_breaker: bool = True,
        **kwargs,
    ) -> T:
        """Run fn(*args, **kwargs) under retry + (optionally) a named
        circuit breaker. `name` identifies the dependency for breaker
        bookkeeping and log messages (e.g. "angelone", "telegram",
        "websocket_reconnect", "config_load")."""
        policy = policy or RetryPolicy()
        breaker = self.get_breaker(name, breaker_config) if use_circuit_breaker else None

        last_exc: Optional[Exception] = None
        for attempt in range(policy.max_attempts):
            if breaker is not None:
                breaker.before_call()  # raises CircuitBreakerOpenError if open
            try:
                result = _call_with_timeout(fn, args, kwargs, policy.timeout)
            except policy.retryable_exceptions as exc:  # type: ignore[misc]
                last_exc = exc
                if breaker is not None:
                    breaker.record_failure()
            else:
                if breaker is not None:
                    breaker.record_success()
                return result

            if attempt < policy.max_attempts - 1:
                wait = min(policy.max_backoff, policy.base_backoff * (2 ** attempt))
                wait += random.uniform(0, wait * policy.jitter_fraction)
                time.sleep(wait)

        raise RetryExhaustedError(
            f"'{name}' failed after {policy.max_attempts} attempt(s): {last_exc}",
            last_exception=last_exc,
            attempts=policy.max_attempts,
        )


def _call_with_timeout(fn, args, kwargs, timeout: Optional[float]):
    """Runs fn in-thread if no timeout is set (the common case --
    zero overhead). If a timeout is given, runs it in a daemon thread
    and raises TimeoutError if it doesn't finish in time. Note: a
    timed-out call may still be running in the background (Python
    can't forcibly kill a thread) -- this bounds how long the CALLER
    waits, it doesn't cancel the underlying I/O. That's the same
    limitation every thread-based Python timeout has, and is the right
    tradeoff here vs. the complexity of a subprocess-per-call model."""
    if timeout is None:
        return fn(*args, **kwargs)

    result: dict = {}
    error: dict = {}

    def _target():
        try:
            result["value"] = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - re-raised in caller thread
            error["exc"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"{getattr(fn, '__name__', fn)} did not complete within {timeout}s")
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


# Process-wide default instance -- import this rather than constructing
# your own RetryManager() unless you specifically need isolated breaker
# state (e.g. per-test).
retry_manager = RetryManager()


def retry(
    name: str,
    policy: Optional[RetryPolicy] = None,
    breaker_config: Optional[CircuitBreakerConfig] = None,
    use_circuit_breaker: bool = True,
):
    """Decorator form of retry_manager.call(), for new call sites.

        @retry(name="replay_loading", policy=RetryPolicy(max_attempts=4))
        def load_replay_journal(path):
            ...
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return retry_manager.call(
                fn, *args, name=name, policy=policy,
                breaker_config=breaker_config,
                use_circuit_breaker=use_circuit_breaker, **kwargs,
            )
        return wrapper
    return decorator
