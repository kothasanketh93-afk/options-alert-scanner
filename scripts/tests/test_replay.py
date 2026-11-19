import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "research"))
import replay as R

C = lambda *rows: [(f"09:{30+i}", list(r)) for i, r in enumerate(rows)]   # (o,h,l,c)


def test_sl_first_on_tie():
    c = C((100, 130, 90, 100))                      # touches SL(92) and T1(120) same candle -> SL
    assert R.replay(c, "09:30", 100, 92, 120, 140)[0] == "SL_HIT"


def test_t2_and_eod_and_t1_then_sl():
    assert R.replay(C((100, 121, 99, 120), (120, 141, 119, 140)), "09:30", 100, 92, 120, 140) == ("T2_HIT", 140)
    assert R.replay(C((100, 105, 99, 104)), "09:30", 100, 92, 120, 140) == ("EOD_CLOSED", 104)
    st, _ = R.replay(C((100, 121, 99, 118), (118, 119, 91, 95)), "09:30", 100, 92, 120, 140)
    assert st == "T1_THEN_SL"


def test_be_after_t1_and_scales():
    c = C((100, 121, 99, 118), (118, 119, 99.5, 101))
    assert R.replay(c, "09:30", 100, 92, 120, 140, be_after_t1=True) == ("T1_THEN_SL", 100)
    assert R.replay(c, "09:30", 100, 92, 120, 140, sl_scale=0.5)[0] == "T1_HIT_THEN_EOD"   # stop 96 never touched
