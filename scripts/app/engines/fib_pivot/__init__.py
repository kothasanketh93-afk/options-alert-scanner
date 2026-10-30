"""
app/engines/fib_pivot/ -- Fibonacci Pivot + EMA9 + Candle-Close-Confirmation
Shadow Engine (added 2026-10-30).
------------------------------------------------------------------------------
Implements the strategy described in an Instagram reel the user shared
("market anna" channel): 9-period EMA for trend direction, classic
Fibonacci-ratio pivot points (Pivot Point Standard, Fibonacci type) as
support/resistance levels, and a CANDLE-CLOSE-ABOVE/BELOW-LEVEL rule as
entry confirmation (not a mere intrabar touch), computed on 5 or 15
minute candles.

Why this exists as a SEPARATE, parallel engine instead of being folded
into scanner.py's existing Trade Score Engine or app/engines/early_breakout/:

  1. No historical backtest was possible for this exact rule set in this
     environment -- no raw OHLC candle history was available in any of
     the already-collected logs, and this sandbox's network egress is
     restricted to package registries/GitHub, so it cannot reach a
     market-data API (Yahoo Finance, etc.) to fetch one. The reel's own
     cherry-picked example charts are not statistical evidence of an
     edge.
  2. The only honest way to evaluate this strategy is to collect its own
     FORWARD-looking shadow data, the same way PROBABILITY_SHADOW_MODE_
     ENABLED already does for the main engine's unscored candidates
     (main.py's create_shadow_candidates()/run_shadow_tracker_step()).
  3. This engine's shadow data must stay COMPLETELY SEPARATE from the
     existing SHADOW_* log tags and state["shadow_trades"] -- mixing the
     two would corrupt both the already-validated main-engine shadow
     dataset (N=139, see this session's own analysis) and any future
     analysis of this new strategy. Every log line this engine emits
     uses the "FIBPIVOT_SHADOW_" prefix, and all trade records live in
     their own state["fib_pivot_shadow_trades"] dict -- see main.py's
     create_fib_pivot_shadow_candidates()/run_fib_pivot_shadow_step().

THIS ENGINE NEVER SENDS A TELEGRAM ALERT AND NEVER PLACES OR SUGGESTS A
REAL TRADE. It only logs candidates and tracks their hypothetical
outcome, exactly like the existing Phase-2 Shadow/Validation Mode. Do
not wire a live alert path onto this engine until its own shadow data
has been collected and reviewed for long enough to say something
evidence-based about it -- repeating the exact methodological mistake
(small-N, one-sided conclusions) already caught and corrected once this
session for the main engine would be worse here, since this strategy
starts with ZERO prior data of its own.

Modules:
  pivot_calc.py          -- classic Fibonacci-ratio pivot point formula
  close_confirmation.py  -- "close beyond the level" entry-trigger logic
  engine.py              -- per-symbol/direction setup detection, entry
                             point: detect_all_fib_pivot_setups()

LAZY, not eager (PEP 562 module __getattr__) -- same pattern and same
reason as app/engines/early_breakout/__init__.py: engine.py imports
market_data.py, which imports the broker stack (angelone.py -> auth.py
-> pyotp), which needs real credentials/dependencies to even import.
Eagerly `from .engine import ...` here would make importing this
package's pure-math submodules directly (as tests/test_pivot_calc.py
and tests/test_close_confirmation.py do) silently pull in the entire
broker stack too. Resolving the public names lazily, only when
main.py actually accesses them, keeps those two test files genuinely
dependency-free.
"""

__all__ = ["detect_all_fib_pivot_setups", "get_last_scan_stats"]


def __getattr__(name):
    if name in __all__:
        from . import engine
        value = getattr(engine, name)
        globals()[name] = value  # cache on the module so repeated access skips __getattr__
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
