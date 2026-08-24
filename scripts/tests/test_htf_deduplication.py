"""
tests/test_htf_deduplication.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers the 2026-08-11 "review this diff" pass: removing the duplicated
"HTF"/"mtf_confirmed" signal (which was never independent of the 15-min EMA
direction read -- see filters.directional_confirmation_status()'s docstring
for the full trace) and decoupling EMA warmup from ATR's wall-clock cutoff
(market_data.get_ema_warmup_candles(), new in this pass).

Required cases (verbatim from the review):
  A. 15m EMA has sufficient warmup candles -> EMA Ready
  B. Same situation after 11:00 while today's candles are still insufficient
     -> EMA must NOT regress to Not Ready solely because of the cutoff
  C. m5 confirmation + 15m confirmation -> valid structural evidence
  D. 15m confirmation duplicated into mtf_confirmed -> must NOT count as two
     independent structural confirmations
  E. Genuine independent HTF confirmation, if supported -> should count
     independently
  F. No structural evidence -> NO ALERT even if trend + VWAP + volume
     produce 2/5

Run with: python scripts/tests/test_htf_deduplication.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import os
import sys
from datetime import datetime, timezone, timedelta

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

import config
import filters
import market_data

IST = timezone(timedelta(hours=5, minutes=30))


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
# A. 15m EMA has sufficient warmup candles -> EMA Ready
# ---------------------------------------------------------------------------

def test_a_ema_ready_with_sufficient_warmup_candles():
    today_candles = [{"close": 100.0 + i} for i in range(4)]  # only 4 today -- not enough alone
    previous_session_candles = [{"close": 90.0 + i} for i in range(20)]  # 20 tail candles

    with _Patch(market_data, "_broker", _FakeBroker(previous_session_candles)):
        market_data._atr_warmup_cache._store.clear()
        combined, count = market_data.get_ema_warmup_candles(
            "TEST|A", "15minute", today_candles, min_candles=21,
        )
    assert count == 24, f"expected 20 previous-session + 4 today = 24, got {count}"
    assert count >= 21, "A: EMA21 must be computable from this combined set"
    print("test_a_ema_ready_with_sufficient_warmup_candles passed")


# ---------------------------------------------------------------------------
# B. Same situation after 11:00 while today's candles are still insufficient
#    -> EMA must NOT regress to Not Ready solely because of the cutoff
# ---------------------------------------------------------------------------

class _FakeBroker:
    def __init__(self, previous_session_candles):
        self._candles = previous_session_candles

    def get_previous_session_candles(self, instrument_key, interval, count):
        return self._candles


def test_b_ema_and_atr_warmup_both_data_based_no_wall_clock_cutoff():
    """
    BUGFIX (2026-08-20, root-cause audit): get_atr_warmup_candles() used
    to stop merging warmup candles at config.ATR_WARMUP_CUTOFF_HOUR/
    MINUTE (11:00 IST) regardless of data sufficiency -- this test used
    to document that as EXPECTED, unchanged ATR behavior, contrasted
    against EMA's already-fixed data-based cutoff. That wall-clock
    mechanism was removed from get_atr_warmup_candles() too (matching a
    live-reported "candles_fetched=7, atr_ready=False" symptom exactly
    -- 9:15-11:00 IST is precisely 7 fifteen-minute candles). Both
    functions are now data-based and behave consistently: simulating
    "now" well past the old 11:00 cutoff must NOT stop either one from
    merging warmup candles when today's own data is still insufficient.
    """
    today_candles = [{"close": 100.0 + i} for i in range(4)]
    previous_session_candles = [{"close": 90.0 + i} for i in range(20)]

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 11, 11, 5, 0, tzinfo=IST)

    with _Patch(market_data, "_broker", _FakeBroker(previous_session_candles)), \
         _Patch(market_data, "datetime", _FixedDatetime):
        market_data._atr_warmup_cache._store.clear()
        atr_combined, atr_count = market_data.get_atr_warmup_candles(
            "TEST|B", "15minute", today_candles,
        )
    assert atr_count == 24, (
        f"ATR warmup must NOT regress to today-only just because the clock is "
        f"past the old 11:00 cutoff while today's own candles ({len(today_candles)}) "
        f"are still short of the 15-candle need -- got {atr_count}"
    )

    # get_ema_warmup_candles(): same insufficient today_candles, same
    # available previous-session data, NO time patch applied at all --
    # its cutoff is data-based, so it doesn't matter what time it is.
    with _Patch(market_data, "_broker", _FakeBroker(previous_session_candles)):
        market_data._atr_warmup_cache._store.clear()
        ema_combined, ema_count = market_data.get_ema_warmup_candles(
            "TEST|B2", "15minute", today_candles, min_candles=21,
        )
    assert ema_count == 24, (
        f"B: EMA warmup must NOT regress to today-only just because the "
        f"clock crossed 11:00 while today's own candles ({len(today_candles)}) "
        f"are still short of the 21-candle need -- got {ema_count}"
    )
    print("test_b_ema_and_atr_warmup_both_data_based_no_wall_clock_cutoff passed")


def test_b_variant_ema_warmup_stops_once_today_alone_is_sufficient():
    """Companion check: once today's OWN candles reach min_candles, warmup
    candles are correctly dropped (today-only preferred once sufficient --
    same posture ATR's cutoff has, just measured by data, not clock)."""
    today_candles = [{"close": 100.0 + i} for i in range(21)]  # already enough alone
    previous_session_candles = [{"close": 90.0 + i} for i in range(20)]

    with _Patch(market_data, "_broker", _FakeBroker(previous_session_candles)):
        market_data._atr_warmup_cache._store.clear()
        combined, count = market_data.get_ema_warmup_candles(
            "TEST|B3", "15minute", today_candles, min_candles=21,
        )
    assert count == 21, f"today-only already sufficient -- should not merge warmup, got {count}"
    print("test_b_variant_ema_warmup_stops_once_today_alone_is_sufficient passed")


# ---------------------------------------------------------------------------
# C. m5 confirmation + 15m confirmation -> valid structural evidence
# ---------------------------------------------------------------------------

def test_c_m5_plus_15m_confirmation_is_valid_structural_evidence():
    status = filters.directional_confirmation_status(
        ema_direction="PE", trend_value=None, momentum_value=None,
        vwap_confirmed_flag=False, direction="PE", m5_direction="PE",
    )
    assert status["structural_confirmed_count"] == 2, "C: both EMA(15m) and EMA(5m) should read Confirmed"
    assert status["structural_ok"] is True
    print("test_c_m5_plus_15m_confirmation_is_valid_structural_evidence passed")


# ---------------------------------------------------------------------------
# D. 15m confirmation duplicated into mtf_confirmed -> must NOT count as
#    two independent structural confirmations
# ---------------------------------------------------------------------------

def test_d_no_way_to_pass_a_duplicate_htf_signal_into_the_gate():
    """
    Confirms the double-count is structurally impossible now, not just
    coincidentally absent: directional_confirmation_status() no longer
    accepts an mtf_confirmed/htf argument at all (TypeError if one is
    passed), and its returned `detail` dict has no "htf" key -- so there
    is no code path left that could feed a duplicate of ema_direction
    back in under a different name.
    """
    try:
        filters.directional_confirmation_status(
            ema_direction="CE", mtf_confirmed=True, trend_value=None,
            momentum_value=None, vwap_confirmed_flag=False, direction="CE",
        )
        raised = False
    except TypeError:
        raised = True
    assert raised, "D: passing a separate mtf_confirmed/htf argument must be rejected outright (no such parameter)"

    status = filters.directional_confirmation_status(
        ema_direction="CE", trend_value=None, momentum_value=None,
        vwap_confirmed_flag=False, direction="CE",
    )
    assert "htf" not in status["detail"], "D: no 'htf' key should exist in the detail dict at all"
    assert status["count"] == 1, (
        "D: a single EMA(15m) confirmation must count ONCE, not twice under two names -- "
        f"got count={status['count']}"
    )
    print("test_d_no_way_to_pass_a_duplicate_htf_signal_into_the_gate passed")


# ---------------------------------------------------------------------------
# E. Genuine independent HTF confirmation, if supported -> should count
#    independently
# ---------------------------------------------------------------------------

def test_e_no_genuine_independent_htf_source_exists_today():
    """
    Documents the current, honest state rather than asserting a feature
    that doesn't exist: this repository has never fetched candles beyond
    5-minute/15-minute intervals anywhere (confirmed via a repo-wide grep
    for get_recent_underlying_candles()/get_previous_session_candles()
    call sites -- both interval arguments used are always "5minute" or
    "15minute"). filters.directional_confirmation_status() therefore has
    no parameter for a genuine higher-timeframe signal today -- it was
    removed rather than kept as a permanently-fake input. If a real
    higher-timeframe source is added later (e.g. 30-min/60-min candles),
    it belongs back in this function as its own new, independently-
    sourced argument -- this test's job is to catch the OLD failure mode
    (a duplicate of ema_direction masquerading as that argument) recurring,
    not to block a genuine future addition.
    """
    import inspect
    sig = inspect.signature(filters.directional_confirmation_status)
    assert "mtf_confirmed" not in sig.parameters
    assert "htf_direction" not in sig.parameters  # a plausible future genuine-HTF param name
    print("test_e_no_genuine_independent_htf_source_exists_today passed (documents current state)")


# ---------------------------------------------------------------------------
# F. No structural evidence -> NO ALERT even if trend + VWAP + volume
#    produce 2/5 (now 2/4)
# ---------------------------------------------------------------------------

def test_f_no_structural_evidence_blocks_even_with_count_met():
    status = filters.directional_confirmation_status(
        ema_direction=None, trend_value=0.6, momentum_value=None,
        vwap_confirmed_flag=True, direction="CE", m5_direction=None,
    )
    assert status["count"] == 2, "trend + vwap should still count toward the total"
    assert status["structural_ok"] is False, "F: no real EMA-structure evidence exists"
    assert status["passed"] is False, "F: NO ALERT despite count>=required"
    print("test_f_no_structural_evidence_blocks_even_with_count_met passed")


if __name__ == "__main__":
    test_a_ema_ready_with_sufficient_warmup_candles()
    test_b_ema_and_atr_warmup_both_data_based_no_wall_clock_cutoff()
    test_b_variant_ema_warmup_stops_once_today_alone_is_sufficient()
    test_c_m5_plus_15m_confirmation_is_valid_structural_evidence()
    test_d_no_way_to_pass_a_duplicate_htf_signal_into_the_gate()
    test_e_no_genuine_independent_htf_source_exists_today()
    test_f_no_structural_evidence_blocks_even_with_count_met()
    print("\nAll HTF-deduplication / EMA-warmup-decoupling tests (A-F) passed.")
