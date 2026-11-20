import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "research"))
import conflict as C

def row(t, strat, d, st="SL_HIT", sym="NIFTY", strike="25200"):
    return {"date": "2026-11-19", "symbol": sym, "strategy": strat, "direction": d, "alert_time": f"2026-11-19T{t}:00+05:30",
            "final_status": st, "result_pct": "-9", "strike": strike}

def test_opposite_vs_same_and_collapse():
    rows = [row("09:35", "sweep", "CE", "NOT_ENTERED"), row("09:40", "fib_pivot", "PE"), row("09:40", "fib_pivot", "PE", strike="25250"),
            row("09:50", "oi_flow", "CE", "T2_HIT"), row("11:00", "fib_pivot", "PE")]
    res, _ = C.analyse(rows, 15)
    assert len(res["opposite"]) == 1 and len(res["same"]) == 1      # sibling collapsed, 11:00 outside window
