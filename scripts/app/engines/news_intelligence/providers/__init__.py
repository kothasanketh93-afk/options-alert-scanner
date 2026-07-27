"""
app/engines/news_intelligence/providers/ -- one module per FREE news source.
------------------------------------------------------------------------------
Every provider implements BaseProvider.fetch_raw() -> list[dict] and
BaseProvider.name. collector/collector.py fans out over
`get_enabled_providers()` below -- adding a new source means adding one new
file here and one line in that registry function, NEVER touching an
existing provider file (open/closed, per this module's SOLID requirement).

All sources are free, public, no-API-key endpoints: NSE/BSE corporate
announcement JSON feeds, RBI/SEBI public press-release listings, and free
RSS feeds. No premium/paid data vendor is used anywhere in this package.
"""

from .base_provider import BaseProvider
from .nse_provider import NSEProvider
from .bse_provider import BSEProvider
from .rbi_provider import RBIProvider
from .sebi_provider import SEBIProvider
from .rss_provider import RSSProvider


def get_enabled_providers(settings=None) -> list[BaseProvider]:
    """Returns provider instances whose settings.providers.<name>.enabled
    is True. This is the ONLY function that needs editing to add/remove a
    provider from the live rotation -- individual provider classes never
    need to know about each other."""
    from ..config import get_settings

    settings = settings or get_settings()
    registry = [
        ("nse", NSEProvider),
        ("bse", BSEProvider),
        ("rbi", RBIProvider),
        ("sebi", SEBIProvider),
        ("rss", RSSProvider),
    ]
    enabled = []
    for key, cls in registry:
        provider_cfg = getattr(settings.providers, key)
        if getattr(provider_cfg, "enabled", True):
            enabled.append(cls(provider_cfg))
    return enabled


__all__ = [
    "BaseProvider", "NSEProvider", "BSEProvider", "RBIProvider", "SEBIProvider",
    "RSSProvider", "get_enabled_providers",
]
