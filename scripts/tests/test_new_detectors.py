from app.engines.sweep_reversal import signals as s
import mcx_signals


def C(i, o, h, l, c):
    hh, mm = divmod(9 * 60 + 15 + 5 * i, 60)
    return {"timestamp": f"2026-11-13T{hh:02d}:{mm:02d}:00+05:30", "open": o, "high": h, "low": l, "close": c}


def pdh_series():
    cs = [C(i, 98, 99, 97, 98) for i in range(6)]          # below PDH=100
    cs.append(C(6, 98, 101.5, 98, 101))                    # break
    cs.append(C(7, 101, 102, 100.8, 101.8))                # drift up
    cs.append(C(8, 101.8, 102, 100.5, 101.5))             # pullback not reaching level
    cs.append(C(9, 100.5, 102, 99.8, 101.6))               # retest candle bullish -> signal
    return cs


def test_pdh_retest_signal_and_mirror():
    cs = pdh_series()
    sig = s.detect_pdhl_retest(cs, 100, 50, 2.0)
    assert sig and sig["direction"] == "CE" and sig["level_name"] == "PDH" and sig["signal_index"] == 9
    # mirror: negate prices, PDL = -100
    m = [{"timestamp": c["timestamp"], "open": 200 - c["open"], "high": 200 - c["low"], "low": 200 - c["high"], "close": 200 - c["close"]} for c in cs]
    sg = s.detect_pdhl_retest(m, 500, 100, 2.0)
    assert sg and sg["direction"] == "PE" and sg["level_name"] == "PDL"


def test_pdh_no_lookahead_and_dead_level():
    cs = pdh_series()
    assert s.detect_pdhl_retest(cs[:9], 100, 50, 2.0) is None          # retest not yet formed
    dead = cs[:8] + [C(8, 101, 101, 99, 99.5)] + [C(9, 99.5, 102, 99.4, 101.6)]
    assert s.detect_pdhl_retest(dead, 100, 50, 2.0) is None            # closed back below level -> dead


def test_pdh_window_gate():
    cs = pdh_series()
    assert s.detect_pdhl_retest(cs, 100, 50, 2.0, window=(15 * 60, 16 * 60)) is None


def vwap_series():
    cs = [C(i, 100 + i * 0.3, 100.6 + i * 0.3, 99.8 + i * 0.3, 100.4 + i * 0.3) for i in range(19)]
    last = cs[-1]["close"]
    cs.append(C(19, last, last + 0.1, 99.0, last - 0.2))
    return cs


def test_vwap_pullback_signal():
    cs = vwap_series()
    base = cs[-1]
    cs.append(C(20, base["close"], base["close"] + 1.5, base["close"] - 0.1, base["close"] + 1.2))
    cs = [dict(c, timestamp=f"2026-11-13T{(10 + (i * 5) // 60):02d}:{(i * 5) % 60:02d}:00+05:30") for i, c in enumerate(cs)]
    cs = [dict(c, timestamp=f"2026-11-13T{h:02d}:{m:02d}:00+05:30") for c, (h, m) in zip(cs, [divmod(9 * 60 + 15 + 5 * i, 60) for i in range(len(cs))])]
    sig = s.detect_vwap_pullback(cs, window=(0, 24 * 60))
    assert sig and sig["direction"] == "CE" and sig["level_name"] == "VWAP"


def test_vwap_no_signal_on_flat_or_short():
    assert s.detect_vwap_pullback([C(i, 100, 101, 99, 100) for i in range(10)], window=(0, 1440)) is None
    assert s.detect_vwap_pullback([C(i, 100, 101, 99, 100) for i in range(30)], window=(0, 1440)) is None


def test_mcx_wrappers_exist():
    assert mcx_signals.detect_pdhl_retest([], 1, 1, 1.0) is None
    assert mcx_signals.detect_vwap_pullback([]) is None
