import csv

import strategy_intel as SI

COLS = ["trade_id", "engine", "strategy", "symbol", "direction", "date", "alert_time", "expiry_day", "net_result_pct",
        "realistic_net_pct", "entry_price", "final_status"]


def _write(p, rows):
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)


def _rows():
    return [["a", "fib_pivot", "fib_pivot", "NIFTY", "CE", "2026-11-09", "2026-11-09T10:00:00+05:30", "False", 10, 8, 100, "T2_HIT"],
            ["b", "fib_pivot", "fib_pivot", "NIFTY", "CE", "2026-11-09", "2026-11-09T10:05:00+05:30", "False", 10, 8, 200, "T2_HIT"],   # same signal, other strike
            ["c", "fib_pivot", "fib_pivot", "NIFTY", "PE", "2026-11-09", "2026-11-09T12:00:00+05:30", "False", -10, -12, 100, "SL_HIT"],
            ["a", "fib_pivot", "fib_pivot", "NIFTY", "CE", "2026-11-09", "2026-11-09T10:00:00+05:30", "False", 10, 8, 100, "T2_HIT"],   # duplicate id
            ["d", "fib_pivot", "fib_pivot", "NIFTY", "CE", "2026-11-09", "x", "False", 5, 4, 100, "NOT_ENTERED"],
            ["e", "live", "trade_score", "SENSEX", "CE", "2026-11-09", "2026-11-09T09:40:00+05:30", "True", 20, 18, 100, "T2_HIT"],
            ["f", "fib_pivot", "fib_pivot", "RELIANCE", "CE", "2026-11-09", "2026-11-09T09:40:00+05:30", "False", 20, 18, 100, "T2_HIT"]]


def test_load_drops_duplicates_not_entered_and_stocks(tmp_path):
    p = tmp_path / "o.csv"
    _write(p, _rows())
    t = SI.load_trades(p)
    assert sorted(x["id"] for x in t) == ["a", "b", "c", "e"]


def test_rupee_math_matches_lot_size(tmp_path):
    p = tmp_path / "o.csv"
    _write(p, _rows())
    a = next(x for x in SI.load_trades(p) if x["id"] == "a")
    assert round(a["inr"], 2) == round(10 / 100 * 100 * 65, 2)


def test_live_and_shadow_are_separate(tmp_path):
    p = tmp_path / "o.csv"
    _write(p, _rows())
    rows = SI.report_rows(SI.load_trades(p))
    assert {r["source"] for r in rows} == {"shadow", "live"}


def test_dedupe_clusters_counts_one_bet_per_signal(tmp_path):
    p = tmp_path / "o.csv"
    _write(p, _rows())
    sh = [x for x in SI.load_trades(p) if x["src"] == "shadow"]
    assert len(sh) == 3 and len(SI.dedupe_clusters(sh)) == 2


def test_metrics_drawdown_and_streak():
    ts = [{"inr": v, "real_inr": None} for v in (100, -50, -70, -10, 200)]
    m = SI.metrics(ts)
    assert m["net"] == 170 and m["max_dd"] == 130 and m["max_loss_streak"] == 3 and m["pf"] == round(300 / 130, 2)


def test_concentration_and_reports_written(tmp_path):
    p = tmp_path / "o.csv"
    _write(p, _rows())
    t = SI.load_trades(p)
    c = SI.concentration([x for x in t if x["src"] == "shadow"], top=1)
    assert c["net_without_top"] == c["net"] - c["top_sum"]
    out = SI.write_reports(t, tmp_path / "out")
    assert out.exists() and (tmp_path / "out" / "strategy_by_instrument.csv").exists()
