import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.engines.sweep_reversal import signals as sg


def C(h, m, o, hi, lo, c):
    return {"timestamp": f"2026-11-06T{h:02d}:{m:02d}:00+05:30", "open": o, "high": hi, "low": lo, "close": c, "volume": 1}


def _series(n, start=100.0, step=0.1, t0=9 * 60 + 15):
    out = []
    for i in range(n):
        c, t = start + step * i, t0 + 5 * i
        out.append(C(t // 60, t % 60, c - 0.05, c + 0.1, c - 0.1, c))
    return out


def test_gap_orb_gap_up_holds_and_breaks_out():
    cs = [C(9, 15, 101.0, 101.4, 100.8, 101.2), C(9, 20, 101.2, 101.5, 101.0, 101.3), C(9, 25, 101.3, 101.6, 101.1, 101.5),
          C(9, 30, 101.5, 101.6, 101.3, 101.4), C(9, 35, 101.5, 102.4, 101.5, 102.3)]
    sig = sg.detect_gap_orb(cs, 100.0)                       # +1% gap, never revisits 100
    assert sig["direction"] == "CE" and sig["level_value"] == 101.6 and sig["extreme"] == 100.8 and sig["gap_pct"] == 1.0
    assert sg.detect_gap_orb(cs, 101.0) is None              # gap < 0.3%
    filled = [C(9, 15, 101.0, 101.4, 99.9, 101.2)] + cs[1:]
    assert sg.detect_gap_orb(filled, 100.0) is None          # gap filled inside the opening range


def test_gap_orb_gap_down_and_late_signal():
    cs = [C(9, 15, 99.0, 99.2, 98.6, 98.8), C(9, 20, 98.8, 99.0, 98.5, 98.6), C(9, 25, 98.6, 98.9, 98.4, 98.5),
          C(9, 30, 98.5, 98.7, 98.4, 98.6), C(9, 35, 98.5, 98.5, 97.4, 97.5)]
    assert sg.detect_gap_orb(cs, 100.0)["direction"] == "PE"
    late = cs[:4] + [C(11, 5, 98.5, 98.5, 97.4, 97.5)]
    assert sg.detect_gap_orb(late, 100.0) is None


def test_nse_ema_pullback_long_and_window():
    cs = _series(40)                                          # 09:15 .. 12:30
    top = cs[-1]["close"]
    cs[-3] = C(12, 20, top - 0.4, top - 0.3, top - 1.6, top - 0.5)
    cs[-2] = C(12, 25, top - 0.5, top - 0.2, top - 0.6, top - 0.3)
    cs[-1] = C(12, 30, top - 0.3, top + 0.6, top - 0.4, top + 0.5)
    sig = sg.detect_ema_pullback(cs, sg.NSE_EMA_WINDOW)
    assert sig and sig["direction"] == "CE" and sig["level_name"] == "EMA9"
    assert sg.detect_ema_pullback(cs[:20], sg.NSE_EMA_WINDOW) is None       # too few candles / before 10:30 window
    flat = [C(9 + (15 + 5 * i) // 60, (15 + 5 * i) % 60, 100, 100.1, 99.9, 100) for i in range(40)]
    assert sg.detect_ema_pullback(flat, sg.NSE_EMA_WINDOW) is None          # no trend
