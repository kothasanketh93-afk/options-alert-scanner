"""
app/engines/early_breakout/__init__.py -- public interface.
------------------------------------------------------------------------------
External callers (main.py) should only ever import from here, not reach
into engine.py / base_detector.py / etc. directly -- this is the
"Business modules should communicate through interfaces" rule from the
enterprise brief, applied concretely: two names are the entire public
surface of this engine.

LAZY, not eager (PEP 562 module __getattr__): engine.py imports
market_data.py, which imports the broker stack (angelone.py -> auth.py ->
pyotp), which needs real credentials/dependencies to even import. Eagerly
`from .engine import ...` here would mean importing ANY submodule of this
package -- including base_detector.py, momentum.py, etc., which are pure
math with zero broker dependency -- silently pulls in the entire broker
stack as a side effect, breaking tests/test_engine.py's whole point (pure-
math tests that "run anywhere, including CI" with no broker credentials
needed). Resolving the two public names lazily, only when actually
accessed, keeps importing this package's submodules directly (as the test
file does) genuinely lightweight -- and this is also literally the "Use:
Lazy Loading" performance principle from the enterprise brief, not just a
workaround for the test suite.
"""

__all__ = ["detect_all_early_breakout_setups", "get_last_scan_stats"]


def __getattr__(name):
    if name in __all__:
        from . import engine
        value = getattr(engine, name)
        globals()[name] = value  # cache on the module so repeated access skips __getattr__
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

