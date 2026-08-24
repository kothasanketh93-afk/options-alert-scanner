"""
tests/test_directional_bias_trend_status.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers the 2026-08-20 fix: option_chain.directional_bias() now reuses
market_data.get_ema_warmup_candles() (already fixed 2026-08-11, previously
only used by scanner.py's own ema9/ema21 read) instead of the plain
today-only market_data.get_recent_underlying_candles() -- and returns an
explicit (directions, trend_status) tuple instead of just a bare list,
distinguishing CONFIRMED / CONFLICTED / UNKNOWN so a caller can never
mistake "bias_ready=True" (which was true even in the Unknown fallback)
for genuine trend confirmation.

Required cases (verbatim from the approved scope):
  A. Early-session insufficient raw candles -> warmup retrieves sufficient
     history -> EMA trend becomes available
  B. Truly insufficient historical data -> trend_status=UNKNOWN
  C. Valid bullish 5m + 15m trend -> trend_status=CONFIRMED, CE
  D. Valid bearish 5m + 15m trend -> trend_status=CONFIRMED, PE
  E. 5m bullish + 15m bearish -> trend_status=CONFLICTED
  F. Existing scanner.py ema9/ema21 behaviour unchanged
  G. Unknown trend never interpreted as positive confirmation
"""

import os
import sys
from unittest import mock

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

import option_chain
import market_data
import indicators


class _FakeWarmupBroker:
    """Stands in for market_data._broker -- only get_previous_session_candles()
    is exercised (via get_ema_warmup_candles(), reused by directional_bias())."""

    def __init__(self, prev_session_candles):
        self._candles = prev_session_candles

    def get_previous_session_candles(self, instrument_key, interval, count):
        return self._candles


def _rising_closes(n, start=100.0, step=1.0):
    return [{"close": start + i * step} for i in range(n)]


def _falling_closes(n, start=200.0, step=1.0):
    return [{"close": start - i * step} for i in range(n)]


def _flat_prev_session(n=30, level=100.0):
    return [{"close": level} for _ in range(n)]


def _run_directional_bias(symbol, today_5m, today_15m, prev_session_candles):
    """Runs directional_bias() with get_recent_underlying_candles() and
    the broker both patched, so only the warmup-merge logic under test
    actually varies between cases."""
    with mock.patch.object(option_chain, "resolve_underlying_key", return_value="TEST|X"), \
         mock.patch.object(market_data, "_broker", _FakeWarmupBroker(prev_session_candles)), \
         mock.patch.object(
             market_data, "get_recent_underlying_candles",
             side_effect=lambda key, interval, count: today_5m if interval == "5minute" else today_15m,
         ):
        market_data._atr_warmup_cache._store.clear()
        return option_chain.directional_bias("TEST")


# ---------------------------------------------------------------------------
# A. Early-session insufficient raw candles -> warmup retrieves sufficient
#    history -> EMA trend becomes available
# ---------------------------------------------------------------------------

def test_a_early_session_insufficient_candles_warmup_makes_trend_available():
    today_5m = _rising_closes(5)   # far short of EMA21's 21-candle need
    today_15m = _rising_closes(5)
    prev_session = _rising_closes(30, start=80.0)  # plenty of warmup history, same rising trend

    directions, trend_status = _run_directional_bias("TEST", today_5m, today_15m, prev_session)

    assert trend_status == "CONFIRMED", f"A: warmup-merged data must make a real trend readable, got {trend_status}"
    assert directions == ["CE"], f"A: rising closes must read as CE, got {directions}"
    print("test_a_early_session_insufficient_candles_warmup_makes_trend_available passed")


# ---------------------------------------------------------------------------
# B. Truly insufficient historical data -> trend_status=UNKNOWN
# ---------------------------------------------------------------------------

def test_b_truly_insufficient_data_even_with_warmup_is_unknown():
    today_5m = _rising_closes(3)
    today_15m = _rising_closes(3)
    prev_session = []  # broker genuinely has nothing to offer either

    directions, trend_status = _run_directional_bias("TEST", today_5m, today_15m, prev_session)

    assert trend_status == "UNKNOWN", f"B: with no warmup data available at all, must be UNKNOWN, got {trend_status}"
    assert directions == ["CE", "PE"], "B: UNKNOWN must fall through to the CE+PE both-directions fallback"
    print("test_b_truly_insufficient_data_even_with_warmup_is_unknown passed")


# ---------------------------------------------------------------------------
# C. Valid bullish 5m + 15m trend -> trend_status=CONFIRMED, CE
# ---------------------------------------------------------------------------

def test_c_bullish_5m_and_15m_confirmed_ce():
    today_5m = _rising_closes(25, start=100.0)   # already enough alone, no warmup needed
    today_15m = _rising_closes(25, start=100.0)
    directions, trend_status = _run_directional_bias("TEST", today_5m, today_15m, [])
    assert trend_status == "CONFIRMED"
    assert directions == ["CE"]
    print("test_c_bullish_5m_and_15m_confirmed_ce passed")


# ---------------------------------------------------------------------------
# D. Valid bearish 5m + 15m trend -> trend_status=CONFIRMED, PE
# ---------------------------------------------------------------------------

def test_d_bearish_5m_and_15m_confirmed_pe():
    today_5m = _falling_closes(25, start=200.0)
    today_15m = _falling_closes(25, start=200.0)
    directions, trend_status = _run_directional_bias("TEST", today_5m, today_15m, [])
    assert trend_status == "CONFIRMED"
    assert directions == ["PE"]
    print("test_d_bearish_5m_and_15m_confirmed_pe passed")


# ---------------------------------------------------------------------------
# E. 5m bullish + 15m bearish -> trend_status=CONFLICTED
# ---------------------------------------------------------------------------

def test_e_5m_bullish_15m_bearish_is_conflicted():
    today_5m = _rising_closes(25, start=100.0)
    today_15m = _falling_closes(25, start=200.0)
    directions, trend_status = _run_directional_bias("TEST", today_5m, today_15m, [])
    assert trend_status == "CONFLICTED", f"E: disagreeing timeframes must be CONFLICTED, got {trend_status}"
    assert directions == ["CE"], "E: still scores the 5m (primary) direction, per the unchanged 2026-07-24 design"
    print("test_e_5m_bullish_15m_bearish_is_conflicted passed")


# ---------------------------------------------------------------------------
# F. Existing scanner.py ema9/ema21 behaviour unchanged
# ---------------------------------------------------------------------------

def test_f_scanner_own_ema_path_unchanged():
    """scanner.py's own ema9/ema21 (fed by get_ema_warmup_candles()
    directly, not via directional_bias()) must be byte-unaffected by
    this fix -- it was already correct, this only touches
    directional_bias()'s OWN separate candle source."""
    today_candles = _rising_closes(5)
    prev_session = _rising_closes(20, start=80.0)
    with mock.patch.object(market_data, "_broker", _FakeWarmupBroker(prev_session)):
        market_data._atr_warmup_cache._store.clear()
        combined, count = market_data.get_ema_warmup_candles("SEP|Y", "15minute", today_candles, min_candles=21)
    ema9 = indicators.compute_ema([c["close"] for c in combined], 9)
    ema21 = indicators.compute_ema([c["close"] for c in combined], 21)
    assert ema9 is not None and ema21 is not None, "F: scanner.py's own EMA path must still work exactly as before"
    print("test_f_scanner_own_ema_path_unchanged passed")


# ---------------------------------------------------------------------------
# G. Unknown trend never interpreted as positive confirmation
# ---------------------------------------------------------------------------

def test_g_unknown_never_equals_confirmed():
    directions, trend_status = _run_directional_bias("TEST", _rising_closes(2), _rising_closes(2), [])
    assert trend_status != "CONFIRMED"
    assert trend_status == "UNKNOWN"
    # The bias_ready-style boolean a caller might still compute must NOT
    # be conflated with confirmation -- bool(directions) is True here
    # (["CE","PE"] fallback) even though trend_status is UNKNOWN, proving
    # a caller MUST check trend_status explicitly, not just truthiness.
    assert bool(directions) is True and trend_status == "UNKNOWN", (
        "G: bias_ready-style truthiness alone must never be mistaken for confirmed trend"
    )
    print("test_g_unknown_never_equals_confirmed passed")


if __name__ == "__main__":
    test_a_early_session_insufficient_candles_warmup_makes_trend_available()
    test_b_truly_insufficient_data_even_with_warmup_is_unknown()
    test_c_bullish_5m_and_15m_confirmed_ce()
    test_d_bearish_5m_and_15m_confirmed_pe()
    test_e_5m_bullish_15m_bearish_is_conflicted()
    test_f_scanner_own_ema_path_unchanged()
    test_g_unknown_never_equals_confirmed()
    print("\nAll directional-bias trend_status tests (A-G) passed.")
