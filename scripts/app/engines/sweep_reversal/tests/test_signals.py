import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
from app.engines.sweep_reversal import signals as S


def c(i, o, h, l, cl):
    hh, mm = divmod(9 * 60 + 15 + 5 * i, 60)
    return {"timestamp": f"2026-11-04T{hh:02d}:{mm:02d}:00+05:30", "open": o, "high": h, "low": l, "close": cl}


# PDL = 100. Opening range candles stay above it; candle 4 wicks below 100 and closes back above, bullish.
BASE = [c(0, 105, 107, 103, 104), c(1, 104, 106, 102, 103), c(2, 103, 105, 101.5, 102), c(3, 102, 103, 101, 102)]


def test_bullish_sweep_signal():
    cs = BASE + [c(4, 101, 104, 99, 103.5)]
    sig = S.detect_sweep(cs, prev_high=120, prev_low=100)
    assert sig and sig["direction"] == "CE" and sig["level_name"] == "PDL" and sig["extreme"] == 99
    assert S.detect_sweep_fvg(cs, 120, 100) is None


def test_failed_sweep_is_dead():
    cs = BASE + [c(4, 101, 101.5, 98, 99), c(5, 99, 102, 98.5, 101)]   # no close back above on s or s+1... s+1 closes 101>100 -> reclaim
    assert S.detect_sweep(cs, 120, 100) is not None
    cs2 = BASE + [c(4, 101, 101.5, 98, 99), c(5, 99, 99.5, 97, 98)]
    assert S.detect_sweep(cs2, 120, 100) is None


def test_bearish_mirror():
    cs = [c(0, 95, 97, 93, 96), c(1, 96, 98, 94, 97), c(2, 97, 98.5, 95, 96), c(3, 96, 99, 95, 96),
          c(4, 99, 101, 96, 97)]   # sweeps PDH=100, closes back below, bearish
    sig = S.detect_sweep(cs, prev_high=100, prev_low=80)
    assert sig and sig["direction"] == "PE" and sig["level_name"] == "PDH" and sig["extreme"] == 101


def test_stale_signal_ignored():
    cs = BASE + [c(4, 101, 104, 99, 103.5), c(5, 103, 104, 102, 103.5), c(6, 103.5, 104, 102.5, 103.8)]
    assert S.detect_sweep(cs, 120, 100) is None


def test_fvg_entry_on_retest():
    # sweep+reclaim at 4; displacement candle 5 leaves gap (candle4.high=104 < candle6.low=106); retest candle 7
    cs = BASE + [c(4, 101, 104, 99, 103.5), c(5, 103.5, 108, 103.4, 107.5), c(6, 107.5, 109, 106, 108),
                 c(7, 106.2, 108.5, 105.5, 107)]
    sig = S.detect_sweep_fvg(cs, 120, 100)
    assert sig and sig["direction"] == "CE" and sig["fvg"] == (104, 106) and sig["signal_index"] == 7


def test_option_levels_floor_and_cap():
    lv = S.option_levels(100.0, 0.5, 103.0, 102.9, 10.0, "CE")   # risk floored to 0.5*ATR=5 -> d=2.5
    assert lv["stop_loss"] == 97.5 and lv["target_1"] == 103.75 and lv["target_2"] == 106.25


def test_amd_bullish():
    # accumulation 6 candles in a tight 100..104 box (prev-day range 40 -> 4 <= 0.35*40)
    acc = [c(0, 102, 103, 101, 102), c(1, 102, 104, 101.5, 103), c(2, 103, 103.5, 100.5, 101), c(3, 101, 102.5, 100, 102),
           c(4, 102, 103, 100.5, 101.5), c(5, 101.5, 102.5, 100.2, 102)]
    manip = [c(6, 101.8, 102, 98.5, 99.5), c(7, 99.5, 101.5, 99, 101), c(8, 101, 103.5, 100.8, 103)]  # sweeps 100, reclaims, closes > mid 102
    sig = S.detect_amd(acc + manip, prev_high=120, prev_low=80)
    assert sig and sig["direction"] == "CE" and sig["extreme"] == 98.5 and sig["signal_index"] == 8
    assert S.detect_amd(acc + manip, prev_high=104, prev_low=96) is None   # range not compressed vs prev day


def test_oi_flow_rules():
    base = {"t": 600, "spot": 100.0, "call_oi": 1000, "put_oi": 1000}
    now = {"t": 620, "spot": 103.0, "call_oi": 1000, "put_oi": 1100}
    sig = S.detect_oi_flow([base, now], atr=5.0)
    assert sig and sig["direction"] == "CE" and sig["d_put_oi"] == 100
    now_bear = {"t": 620, "spot": 97.0, "call_oi": 1100, "put_oi": 1000}
    assert S.detect_oi_flow([base, now_bear], 5.0)["direction"] == "PE"
    assert S.detect_oi_flow([base, {"t": 620, "spot": 103.0, "call_oi": 1100, "put_oi": 1100}], 5.0) is None
    assert S.detect_oi_flow([base, {"t": 605, "spot": 103.0, "call_oi": 1000, "put_oi": 1100}], 5.0) is None  # too recent


def test_standalone_fvg_bull_and_bear():
    # k=3: big bullish candle 100->108 (range 9 >= ATR 5), gap: c2.high=101 < c4.low=106; retest candle 5 touches 106 and closes bullish
    cs = [c(0, 100, 101, 99, 100), c(1, 100, 101, 99.5, 100.5), c(2, 100.5, 101, 99.8, 100.8),
          c(3, 100.8, 109, 100, 108.5), c(4, 108.5, 110, 106, 109), c(5, 107, 108, 105.8, 107.8)]
    sig = S.detect_fvg(cs, atr=5.0)
    assert sig and sig["direction"] == "CE" and sig["fvg"] == (101.0, 106.0) and sig["extreme"] == 100
    mir = [{"timestamp": x["timestamp"], "open": 200 - x["open"], "high": 200 - x["low"], "low": 200 - x["high"],
            "close": 200 - x["close"]} for x in cs]
    sb = S.detect_fvg(mir, atr=5.0)
    assert sb and sb["direction"] == "PE" and sb["extreme"] == 100
    assert S.detect_fvg(cs, atr=20.0) is None      # displacement not big enough vs ATR
