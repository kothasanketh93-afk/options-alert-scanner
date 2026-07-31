"""
tests/test_atr_warmup.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as
tests/test_risk_management_validation_mode.py (no pytest dependency).

Covers FIX 2 (2026-07-30): market_data.get_atr_warmup_candles().

  - config.WARMUP_CANDLE_CONTINUITY_ENABLED default (False): output is
    byte-identical (same list object, same count) to the input
    `today_candles` -- production behavior is completely unchanged
    until this flag is explicitly turned on.
  - Enabled, but at/after the warmup cutoff time: also unchanged --
    today's own candles are treated as already sufficient.
  - Enabled and before the cutoff: previous-session tail candles (from
    the broker's get_previous_session_candles()) are PREPENDED in
    front of today_candles, and combined_candle_count reflects the
    combined length.
  - Any broker exception, or an empty broker result, fails OPEN to
    today-only candles -- never raises, never blocks the caller.
  - Nothing here ever touches get_recent_underlying_candles() (Opening
    Range / today's High-Low / Session VWAP / breakout-level source),
    confirming the "only for indicator calculations" contract.

NOTE: pyotp (a real project dependency -- see requirements.txt) isn't
installed in this test sandbox, so it's stubbed here purely to allow
importing the real angelone.py/market_data.py -- nothing about
angelone.py's own login logic is touched or exercised by these tests.

Run with: python scripts/tests/test_atr_warmup.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import os
import sys
import types
from datetime import datetime, timezone, timedelta

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

if "pyotp" not in sys.modules:
    _pyotp_stub = types.ModuleType("pyotp")

    class _FakeTOTP:
        def __init__(self, *a, **k):
            pass

        def now(self):
            return "000000"

    _pyotp_stub.TOTP = _FakeTOTP
    sys.modules["pyotp"] = _pyotp_stub

import config
import market_data
from cache import TTLCache

IST = timezone(timedelta(hours=5, minutes=30))


class _FakeBroker:
    """Stands in for market_data._broker -- only get_previous_session_candles()
    is exercised by these tests."""

    def __init__(self, candles=None, raise_exc=None):
        self._candles = candles if candles is not None else []
        self._raise_exc = raise_exc
        self.calls = 0

    def get_previous_session_candles(self, instrument_key, interval, count):
        self.calls += 1
        if self._raise_exc:
            raise self._raise_exc
        return self._candles


def _some_today_candles(n=6):
    return [
        {"timestamp": f"2026-07-30T{9 + i // 4:02d}:{15 + (i % 4) * 15:02d}:00+05:30",
         "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0}
        for i in range(n)
    ]


def _prev_session_candles(n=20):
    return [
        {"timestamp": f"2026-07-29T{9 + i // 4:02d}:{15 + (i % 4) * 15:02d}:00+05:30",
         "open": 98.0, "high": 99.0, "low": 97.0, "close": 98.5, "volume": 500.0}
        for i in range(n)
    ]


def _reset(flag_enabled=False, cutoff_hour=11, cutoff_minute=0, broker=None):
    config.WARMUP_CANDLE_CONTINUITY_ENABLED = flag_enabled
    config.ATR_WARMUP_CUTOFF_HOUR = cutoff_hour
    config.ATR_WARMUP_CUTOFF_MINUTE = cutoff_minute
    market_data.WARMUP_CANDLE_CONTINUITY_ENABLED = flag_enabled
    market_data.ATR_WARMUP_CUTOFF_HOUR = cutoff_hour
    market_data.ATR_WARMUP_CUTOFF_MINUTE = cutoff_minute
    market_data._atr_warmup_cache = TTLCache(ttl_seconds=180)  # fresh cache, no cross-test pollution
    if broker is not None:
        market_data._broker = broker


_original_broker = market_data._broker
_original_flag = config.WARMUP_CANDLE_CONTINUITY_ENABLED
_original_cutoff_hour = config.ATR_WARMUP_CUTOFF_HOUR
_original_cutoff_minute = config.ATR_WARMUP_CUTOFF_MINUTE


def _restore():
    market_data._broker = _original_broker
    _reset(flag_enabled=_original_flag, cutoff_hour=_original_cutoff_hour,
           cutoff_minute=_original_cutoff_minute)


def test_flag_disabled_returns_today_candles_unchanged():
    today = _some_today_candles()
    _reset(flag_enabled=False, broker=_FakeBroker(candles=_prev_session_candles()))
    try:
        result, count = market_data.get_atr_warmup_candles("NSE_INDEX|Nifty 50", "15minute", today)
        assert result is today, "must be the SAME object -- byte-identical, no copy/merge at all"
        assert count == len(today)
    finally:
        _restore()
    print("test_flag_disabled_returns_today_candles_unchanged passed")


def test_flag_enabled_but_past_cutoff_returns_today_candles_unchanged():
    today = _some_today_candles()
    # Cutoff at 00:00 IST -- "now" (any time after midnight) is always >= cutoff,
    # deterministically exercising the "past cutoff" branch regardless of
    # wall-clock time when this test runs.
    _reset(flag_enabled=True, cutoff_hour=0, cutoff_minute=0,
           broker=_FakeBroker(candles=_prev_session_candles()))
    try:
        result, count = market_data.get_atr_warmup_candles("NSE_INDEX|Nifty 50", "15minute", today)
        assert result is today
        assert count == len(today)
    finally:
        _restore()
    print("test_flag_enabled_but_past_cutoff_returns_today_candles_unchanged passed")


def test_flag_enabled_before_cutoff_prepends_warmup_candles():
    today = _some_today_candles(n=6)
    prev = _prev_session_candles(n=20)
    # Cutoff at 23:59 IST -- "now" is always before this, deterministically
    # exercising the "before cutoff" branch regardless of wall-clock time.
    fake_broker = _FakeBroker(candles=prev)
    _reset(flag_enabled=True, cutoff_hour=23, cutoff_minute=59, broker=fake_broker)
    try:
        result, count = market_data.get_atr_warmup_candles("NSE_INDEX|Nifty 50", "15minute", today)
        assert result == prev + today, "previous-session candles must be PREPENDED, today's candles kept intact"
        assert result[len(prev):] == today, "today's own candles must appear last, unmodified"
        assert count == len(prev) + len(today)
        assert fake_broker.calls == 1
    finally:
        _restore()
    print("test_flag_enabled_before_cutoff_prepends_warmup_candles passed")


def test_broker_exception_fails_open_to_today_only():
    today = _some_today_candles()
    fake_broker = _FakeBroker(raise_exc=RuntimeError("simulated broker failure"))
    _reset(flag_enabled=True, cutoff_hour=23, cutoff_minute=59, broker=fake_broker)
    try:
        result, count = market_data.get_atr_warmup_candles("NSE_INDEX|Nifty 50", "15minute", today)
        assert result is today, "a broker failure must never raise out of this function or block the caller"
        assert count == len(today)
    finally:
        _restore()
    print("test_broker_exception_fails_open_to_today_only passed")


def test_broker_empty_result_fails_open_to_today_only():
    today = _some_today_candles()
    fake_broker = _FakeBroker(candles=[])
    _reset(flag_enabled=True, cutoff_hour=23, cutoff_minute=59, broker=fake_broker)
    try:
        result, count = market_data.get_atr_warmup_candles("NSE_INDEX|Nifty 50", "15minute", today)
        assert result is today
        assert count == len(today)
    finally:
        _restore()
    print("test_broker_empty_result_fails_open_to_today_only passed")


def test_warmup_result_is_cached_within_a_run():
    today = _some_today_candles()
    prev = _prev_session_candles()
    fake_broker = _FakeBroker(candles=prev)
    _reset(flag_enabled=True, cutoff_hour=23, cutoff_minute=59, broker=fake_broker)
    try:
        market_data.get_atr_warmup_candles("NSE_INDEX|Nifty 50", "15minute", today)
        market_data.get_atr_warmup_candles("NSE_INDEX|Nifty 50", "15minute", today)
        assert fake_broker.calls == 1, "second call for the same (instrument_key, interval) must hit the cache, not the broker again"
    finally:
        _restore()
    print("test_warmup_result_is_cached_within_a_run passed")


def test_compute_atr_actually_succeeds_with_warmup_when_today_alone_is_insufficient():
    """
    End-to-end check tying this back to the actual production bug:
    6 candles today alone can never satisfy compute_atr()'s
    15-candle requirement, but 6 + a 20-candle warmup tail does.
    """
    from indicators import compute_atr

    today = _some_today_candles(n=6)
    assert compute_atr(today) is None, "sanity check: today-only candles must be insufficient on their own"

    prev = _prev_session_candles(n=20)
    fake_broker = _FakeBroker(candles=prev)
    _reset(flag_enabled=True, cutoff_hour=23, cutoff_minute=59, broker=fake_broker)
    try:
        combined, _ = market_data.get_atr_warmup_candles("NSE_INDEX|Nifty 50", "15minute", today)
        assert compute_atr(combined) is not None, "ATR must now be computable with warmup candles prepended"
    finally:
        _restore()
    print("test_compute_atr_actually_succeeds_with_warmup_when_today_alone_is_insufficient passed")


if __name__ == "__main__":
    test_flag_disabled_returns_today_candles_unchanged()
    test_flag_enabled_but_past_cutoff_returns_today_candles_unchanged()
    test_flag_enabled_before_cutoff_prepends_warmup_candles()
    test_broker_exception_fails_open_to_today_only()
    test_broker_empty_result_fails_open_to_today_only()
    test_warmup_result_is_cached_within_a_run()
    test_compute_atr_actually_succeeds_with_warmup_when_today_alone_is_insufficient()
    print("\nAll ATR-warmup tests passed.")
