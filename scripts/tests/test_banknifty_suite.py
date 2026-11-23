import csv
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import banknifty_log as L
import banknifty_report as R
import config
import cost_model
import latency
import risk_guard
import spread_plan

IST = latency.IST


def test_cost_model_known_values():
    # entry 100*30=3000, exit 3600 (+20%): brokerage 40, STT 5.4, exch 2.34, sebi .0066, stamp .09, gst 7.62
    ch = cost_model.charges_rs(3000, 3600)
    assert abs(ch - (40 + 5.4 + 0.0355 / 100 * 6600 + 0.0001 / 100 * 6600 + 0.003 / 100 * 3000
                     + 0.18 * (40 + 0.0355 / 100 * 6600 + 0.0001 / 100 * 6600))) < 1e-6
    p = cost_model.trade_pnl(100, 20, 30)
    assert p["gross"] == 600 and p["exit_price"] == 120 and p["net"] < p["gross"]
    assert cost_model.trade_pnl(None, 5, 30)["net"] is None


def _lrow(**kw):
    r = {"date": "2026-11-20", "trade_id": "T1", "engine": "live", "strategy": "trade_score", "symbol": "BANKNIFTY",
         "direction": "CE", "strike": "60000", "expiry": "2026-11-23", "alert_time": "2026-11-20T10:00:00+05:30",
         "entry_price": "100", "alert_premium": "100", "stop_loss": "80", "target_1": "120", "target_2": "140",
         "final_status": "T2_HIT", "result_pct": "40", "spot_at_alert": "60100", "exit_time": "2026-11-20T11:00:00+05:30"}
    r.update(kw)
    return r


def test_log_row_pnl_and_not_entered():
    row = L.to_log_row(_lrow())
    assert row["kind"] == "live" and row["exit_price"] == 140.0 and row["lot_size"] == 30
    assert row["net_pnl_rs"] < row["gross_pnl_rs"] == 1200.0 and row["days_to_expiry"] == 3
    ne = L.to_log_row(_lrow(final_status="NOT_ENTERED", result_pct=""))
    assert ne["net_pnl_rs"] is None and ne["exit_reason"] == "NOT_ENTERED"


def test_rebuild_filters_symbol(tmp_path):
    src = tmp_path / "o.csv"
    with open(src, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(_lrow().keys()))
        w.writeheader()
        w.writerow(_lrow())
        w.writerow(_lrow(trade_id="N1", symbol="NIFTY"))
    out = tmp_path / "bn" / "log.csv"
    assert L.rebuild(src, out) == 1
    assert [r["trade_id"] for r in L.read_log(out)] == ["T1"]


def _log(pnls, kind="live", day="2026-11-20"):
    return [{"kind": kind, "date": day, "alert_time": f"{day}T10:0{i}", "exit_time": f"{day}T11:0{i}", "net_pnl_rs": p,
             "strategy": "s", "option_type": "CE", "strike": "1"} for i, p in enumerate(pnls)]


def test_stats_drawdown_and_winrate():
    s = R.stats(_log([1000, -500, -700, 300, 200]))
    assert s["trades"] == 5 and s["wins"] == 3 and s["win_rate"] == 60.0
    assert s["net"] == 300 and s["max_drawdown"] == 1200       # peak 1000 -> trough -200
    assert s["best"]["net_pnl_rs"] == 1000 and s["worst"]["net_pnl_rs"] == -700


def test_edge_check_needs_trades_and_days():
    few = R.edge_check([("d1", 5000.0)] * 10)
    assert "NOT PROVEN" in few["verdict"]
    many = [(f"d{i}", 500.0 + (i % 3)) for i in range(25) for _ in range(5)]
    assert R.edge_check(many)["verdict"].startswith("POSITIVE")
    noisy = [(f"d{i}", (1 if j % 2 else -1) * 300.0) for i in range(25) for j in range(5)]
    assert "NOT PROVEN" in R.edge_check(noisy)["verdict"]


def test_texts_render():
    rows = _log([500, -300])
    assert "BankNifty daily summary" in R.daily_text(rows, "2026-11-20", rows)
    assert "Edge check" in R.weekly_text(rows, "2026-11-16", "2026-11-20", rows)


def test_latency_stamp_with_and_without_signal_ts():
    t = {"alert_time": "2026-11-20T10:07:30+05:30", "signal_ts": "2026-11-20T10:00:00+05:30"}
    latency.stamp_notified(t, datetime(2026, 11, 20, 10, 8, 0, tzinfo=IST))
    assert t["detect_to_send_sec"] == 30.0 and t["signal_to_send_sec"] == 180.0 and t["signal_time_estimated"] is False
    u = {"alert_time": "2026-11-20T10:07:30+05:30"}
    latency.stamp_notified(u, datetime(2026, 11, 20, 10, 8, 0, tzinfo=IST))
    assert u["signal_to_send_sec"] == 180.0 and u["signal_time_estimated"] is True      # bar boundary 10:05 -> 10:08
    latency.stamp_notified(u, datetime(2026, 11, 20, 12, 0, 0, tzinfo=IST))
    assert u["detect_to_send_sec"] == 30.0                                               # first send wins


def test_daily_loss_limit_blocks(monkeypatch):
    monkeypatch.setattr(config, "RISK_GUARD_ENABLED", True)
    monkeypatch.setattr(config, "RISK_KILL_SWITCH", False)
    monkeypatch.setattr(config, "RISK_DAILY_SL_STOP", 99)
    monkeypatch.setattr(config, "RISK_MAX_TRADES_PER_DAY", None)
    monkeypatch.setattr(config, "RISK_DAILY_LOSS_LIMIT_PCT", 1.5)
    monkeypatch.setattr(config, "PAPER_CAPITAL", 200000)       # limit Rs 3,000
    now = datetime(2026, 11, 20, 11, 0, tzinfo=IST)
    big = {"symbol": "BANKNIFTY", "status": "SL_HIT", "final_status": "SL_HIT", "tracked_entry_price": 200.0,
           "initial_stop_loss": 160.0, "stop_loss": 160.0, "risk_sizing": {"lot_size": 30}}
    ok, _ = risk_guard.check_global({"trades": {}}, now)
    assert ok
    ok, why = risk_guard.check_global({"trades": {"a": big}}, now)       # 200 -> 160 = -20% of 6,000 = -1,200 - costs: under limit
    assert ok
    ok, why = risk_guard.check_global({"trades": {"a": big, "b": dict(big), "c": dict(big)}}, now)
    assert not ok and "daily loss limit" in why


def test_spread_plan_math_and_suggest():
    p = spread_plan.plan("CE", 60000, 300.0, 60300, 180.0, 30)
    assert p["net_debit"] == 120.0 and p["max_loss_rs"] == 3600 and p["max_profit_rs"] == 5400
    assert p["breakeven"] == 60120.0 and p["reward_risk"] == 1.5
    assert spread_plan.plan("CE", 60000, 100.0, 60300, 180.0, 30) is None          # not a debit
    assert spread_plan.sell_strike_for("PE", 60000, 100) == 59700
    trade = {"symbol": "BANKNIFTY", "direction": "PE", "strike": 60000, "ask": 250.0}
    sp = spread_plan.suggest(trade, lambda t: ["chain"], lambda chain, k, d: {"bid": 120.0})
    assert sp["sell_strike"] == 59700 and sp["net_debit"] == 130.0
    assert spread_plan.suggest({"symbol": "NIFTY"}, lambda t: [], lambda *a: None) is None
    assert "Debit-spread" in spread_plan.alert_line(sp) and spread_plan.alert_line(None) == ""


def test_contract_constants():
    assert config.INDEX_LOT_SIZE["BANKNIFTY"] == 30 and config.STRIKE_STEP["BANKNIFTY"] == 100
