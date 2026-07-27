"""
app/engines/news_intelligence/providers/base_provider.py -- the provider
contract every source implements.
------------------------------------------------------------------------------
Design pattern: Strategy. collector/collector.py holds a list of
BaseProvider instances and calls fetch_raw() on each without knowing (or
caring) which concrete source it is -- new sources plug in without any
existing provider or the collector itself changing (SOLID's Open/Closed
principle, explicitly required for this module).

fetch_raw() returns a list of small, source-native dicts (NOT NewsItem --
normalization into NewsItem is parser/normalizer.py's job, not the
provider's). Keeping providers "dumb fetchers" means parser/ has exactly
ONE place to update when a source changes its field names, rather than
scattering normalization logic across five files.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseProvider(ABC):
    """name: short, stable identifier used as NewsItem.source (or a
    prefix, for multi-feed providers like RSSProvider -- see that class).
    """

    name: str = "base"

    def __init__(self, provider_config: Any = None):
        self.config = provider_config

    @abstractmethod
    def fetch_raw(self) -> list[dict]:
        """Returns a list of raw, source-native news dicts. MUST NOT raise
        -- any fetch/parse failure should be caught internally, logged, and
        result in an empty list, so one provider's outage never blocks the
        others in collector.collect_all()'s fan-out. See utils/http_client.py
        for the retry/backoff helper every provider should use for its
        actual network call."""
        raise NotImplementedError

    def _timeout(self, default: float = 8.0) -> float:
        return float(getattr(self.config, "timeout_seconds", default)) if self.config else default

    def _retries(self, default: int = 2) -> int:
        return int(getattr(self.config, "retries", default)) if self.config else default

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
