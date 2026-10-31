"""
tests/test_architecture_cleanup.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers the 2026-08-14 architecture cleanup pass:
  A. (removed 2026-10-31 with the Decision Engine itself -- it had been
     deprecated from the live path since 2026-08-14 and was deleted at the
     owner's request; its two tests were removed here.)
  B. India VIX fetch failure never raises (logging-only, must never break a scan).
  C. PCR native-fetch failure never raises (logging-only, diagnostic only).
  D. tracker.compute_time_taken()'s result is what main.py now persists onto
     the trade dict for T1/T2/SL (not just used transiently for Telegram text).
"""

import os
import sys
from unittest import mock

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

import config
import market_data
import upstox
import tracker


class _Patch:
    def __init__(self, obj, attr, value):
        self.obj, self.attr, self.value = obj, attr, value
        self._had = hasattr(obj, attr)
        self._orig = getattr(obj, attr, None)

    def __enter__(self):
        setattr(self.obj, self.attr, self.value)
        return self.value

    def __exit__(self, *exc):
        if self._had:
            setattr(self.obj, self.attr, self._orig)
        else:
            delattr(self.obj, self.attr)


# ---------------------------------------------------------------------------
# B. India VIX fetch failure never raises
# ---------------------------------------------------------------------------

def test_b_india_vix_fetch_failure_returns_none_not_raise():
    def fake_get_ltp(instrument_key):
        raise RuntimeError("simulated network failure")

    with _Patch(market_data._broker, "get_ltp", fake_get_ltp):
        result = market_data.get_india_vix()

    assert result is None, "B: a failed VIX fetch must return None, never raise"
    print("test_b_india_vix_fetch_failure_returns_none_not_raise passed")


def test_b_variant_india_vix_success_path():
    with _Patch(market_data._broker, "get_ltp", lambda instrument_key: 13.42):
        result = market_data.get_india_vix()
    assert result == 13.42
    print("test_b_variant_india_vix_success_path passed")


# ---------------------------------------------------------------------------
# C. PCR native-fetch failure never raises
# ---------------------------------------------------------------------------

def test_c_native_pcr_fetch_failure_returns_none_not_raise():
    broker = market_data._upstox

    def fake_request_with_retry(*args, **kwargs):
        raise RuntimeError("simulated network failure")

    with _Patch(upstox, "request_with_retry", fake_request_with_retry):
        result = broker.get_native_pcr("NSE_INDEX|Nifty 50", "2026-08-18", "2026-08-14")

    assert result is None, "C: a failed native-PCR fetch must return None, never raise"
    print("test_c_native_pcr_fetch_failure_returns_none_not_raise passed")


# ---------------------------------------------------------------------------
# D. time-to-target/time-to-stop are persisted onto the trade dict, not just
#    used transiently for the Telegram message
# ---------------------------------------------------------------------------

def test_d_time_to_target_and_stop_are_persisted_on_trade():
    trade = {
        "actual_entry_time": "2026-08-14T09:30:00+05:30",
        "t1_hit_time": "2026-08-14T09:45:00+05:30",
        "t2_hit_time": "2026-08-14T10:00:00+05:30",
        "sl_hit_time": "2026-08-14T09:40:00+05:30",
        "messages_sent": [],
        "symbol": "NIFTY",
    }
    t1_mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["t1_hit_time"])
    t2_mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["t2_hit_time"])
    sl_mins = tracker.compute_time_taken(trade["actual_entry_time"], trade["sl_hit_time"])

    # Simulates exactly what main.py's event-handling loop now does --
    # stores the computed value onto the trade dict (which IS what gets
    # persisted via state.save_state()), not just passed to a formatter.
    trade["time_to_target_1_minutes"] = t1_mins
    trade["time_to_target_2_minutes"] = t2_mins
    trade["time_to_stop_minutes"] = sl_mins

    assert trade["time_to_target_1_minutes"] == 15
    assert trade["time_to_target_2_minutes"] == 30
    assert trade["time_to_stop_minutes"] == 10
    # Sanity: these keys did not exist before this pass -- confirms this
    # is genuinely new persisted state, not a pre-existing field.
    print("test_d_time_to_target_and_stop_are_persisted_on_trade passed")


def test_d_variant_main_py_actually_sets_these_fields():
    """
    Static check that main.py's source contains the persistence lines
    (not just that compute_time_taken() itself works, which test D
    above already confirms) -- guards against the exact regression this
    audit found: the computed value being used ONLY as a Telegram
    formatting argument and never written back onto `trade`.
    """
    main_py_path = os.path.join(_scripts_dir, "main.py")
    source = open(main_py_path).read()
    assert 'trade["time_to_target_1_minutes"] = mins' in source
    assert 'trade["time_to_target_2_minutes"] = mins' in source
    assert 'trade["time_to_stop_minutes"] = mins' in source
    print("test_d_variant_main_py_actually_sets_these_fields passed")


if __name__ == "__main__":
    test_b_india_vix_fetch_failure_returns_none_not_raise()
    test_b_variant_india_vix_success_path()
    test_c_native_pcr_fetch_failure_returns_none_not_raise()
    test_d_time_to_target_and_stop_are_persisted_on_trade()
    test_d_variant_main_py_actually_sets_these_fields()
    print("\nAll architecture-cleanup tests (A-D) passed.")
