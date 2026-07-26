"""
app/market/ -- Market Intelligence, one shared snapshot object per
symbol per scan cycle (see snapshot.py's module docstring for full
scope/rationale and what was deliberately left out of this pilot).

Lazy, same reasoning as app.engines.early_breakout's __init__.py:
snapshot.py imports market_data.py, which imports the broker stack
(angelone.py -> auth.py -> pyotp). Resolving get_market_snapshot lazily
means importing this package doesn't require broker credentials to be
present, which matters for anything (tests, tooling) that only needs
the pure config/banding logic without a live broker.
"""

__all__ = ["get_market_snapshot"]


def __getattr__(name):
    if name in __all__:
        from . import snapshot
        value = getattr(snapshot, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

