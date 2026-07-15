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
