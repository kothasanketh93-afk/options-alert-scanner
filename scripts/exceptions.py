"""
exceptions.py
-------------
Every custom exception this project raises, in one place. Before this,
missing credentials, broker failures, and Telegram failures all raised
generic RuntimeError -- indistinguishable from each other and from any
other bug. Nothing in the codebase ever caught RuntimeError specifically
(confirmed by search), so introducing these subclasses changes NO
control flow anywhere: an uncaught exception still fails the GitHub
Actions job exactly as before. What changes is that the job log now
names the actual failure category instead of a bare "RuntimeError".
"""


class TradingSystemError(Exception):
    """Base class for every custom exception this project raises."""


class ConfigurationError(TradingSystemError):
    """Required configuration (env var, secret, instrument key) missing or invalid."""


class AuthenticationError(TradingSystemError):
    """Credentials missing, invalid, or the broker/Telegram rejected them (401/403)."""


class BrokerAPIError(TradingSystemError):
    """A broker API call failed for a reason other than auth or rate limiting."""


class RateLimitError(BrokerAPIError):
    """Broker API returned 429. retry_after (seconds) is set when the
    server told us how long to back off via the Retry-After header."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class InstrumentNotFound(TradingSystemError):
    """A requested instrument (option contract, futures contract) could
    not be resolved -- expired, delisted, or the strike isn't listed."""


class TelegramFailure(TradingSystemError):
    """A Telegram API call failed after retries were exhausted."""


class StateCorruptionError(TradingSystemError):
    """The state JSON file failed to parse. Distinct from
    ConfigurationError so callers can choose to recover (state.py does)
    instead of crashing the whole run."""


# --- Part 9 additions below. Purely additive: nothing above this line
# changed, and no existing `except SomeClass` anywhere in the codebase
# is affected, since these are new subclasses no old code catches or
# raises. ---


class CircuitBreakerOpenError(TradingSystemError):
    """A call was refused immediately because its circuit breaker is
    open (too many recent failures) -- raised instead of attempting
    (and slowly timing out on) a call that's very likely to fail."""

    def __init__(self, message: str, breaker_name: str, retry_after: float | None = None):
        super().__init__(message)
        self.breaker_name = breaker_name
        self.retry_after = retry_after


class RetryExhaustedError(TradingSystemError):
    """All configured retry attempts failed. Wraps the last underlying
    exception (`.last_exception`) so callers/logs keep the original
    root cause instead of losing it behind a generic wrapper."""

    def __init__(self, message: str, last_exception: Exception | None = None,
                 attempts: int | None = None):
        super().__init__(message)
        self.last_exception = last_exception
        self.attempts = attempts


class StartupValidationError(TradingSystemError):
    """One or more critical startup checks failed (missing
    credentials, folders, dependencies, historical data, etc). Raised
    by production.startup_validator to stop the process from starting
    in a broken state, with a full report attached (`.report`)."""

    def __init__(self, message: str, report=None):
        super().__init__(message)
        self.report = report


class ResourceLimitExceeded(TradingSystemError):
    """A monitored resource (memory, CPU, disk, threads, queue size,
    connection count) crossed its configured critical threshold."""

    def __init__(self, message: str, resource: str, value: float, threshold: float):
        super().__init__(message)
        self.resource = resource
        self.value = value
        self.threshold = threshold


class WorkerError(TradingSystemError):
    """A background worker managed by production.task_manager failed
    to start, crashed, or did not shut down cleanly within its
    deadline."""


class CacheError(TradingSystemError):
    """A cache read/write/validate operation failed in a way the
    caller should treat as a cache miss rather than crash on."""
