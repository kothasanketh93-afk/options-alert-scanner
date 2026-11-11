import os, sys, tempfile
_here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(_here))
import sell_view as sv
import eod_report as er
import outcomes_ledger as ol


def _c(hm, close):
    return {"timestamp": f"2026-11-11T{hm}:00+05:30", "open": close, "high": close, "low": close, "close": close}


def _trade(status="SL_HIT", direction="CE", **kw):
    t = {"trade_id": "x1", "symbol": "NIFTY", "direction": direction, "strike": 24000.0, "expiry": "2026-11-13",
         "alert_time": "2026-11-11T09:30:00+05:30", "actual_entry_time": "2026-11-11T09:31:00+05:30",
         "tracked_entry_price": 100.0, "stop_loss": 80.0, "target_1": 120.0, "target_2": 140.0,
         "status": status, "final_status": status, "sl_hit_time": "2026-11-11T09:40:00+05:30",
         "t2_hit_time": "2026-11-11T09:45:00+05:30", "last_processed_ts": "2026-11-11T15:15:00+05:30"}
    t.update(kw)
    return t


def test_opposite_and_exit_time():
    assert sv.opposite("CE") == "PE" and sv.opposite("PE") == "CE"
    assert sv.exit_time(_trade("SL_HIT")).endswith("09:40:00+05:30")
    assert sv.exit_time(_trade("T2_HIT")).endswith("09:45:00+05:30")
    assert sv.exit_time(_trade("EOD_CLOSED")).endswith("15:15:00+05:30")
    assert sv.exit_time(_trade("NOT_ENTERED")) is None and not sv.eligible(_trade("NOT_ENTERED"))
    assert not sv.eligible(_trade("ACTIVE"))


def test_attach_prices_opposite_option_over_same_window():
    t = _trade("SL_HIT")                       # bullish buy of CE stopped out: the PE sold over the same window gained
    fetched = []

    def candles(key, since):
        fetched.append((key, since))
        return [_c("09:30", 50), _c("09:31", 52), _c("09:35", 55), _c("09:40", 60)]
    n = sv.attach_all([t], lambda tr: "NSE_FO|PE24000", candles)
    assert n == 1 and fetched[0][0] == "NSE_FO|PE24000" and fetched[0][1] < t["actual_entry_time"]
    s = t["sell_view"]
    assert s["option"] == "PE 24000.0" and s["entry"] == 52 and s["exit"] == 60
    assert s["pct"] == round((52 - 60) / 52 * 100, 2)   # PE rose -> seller lost
    assert not sv.eligible(t)                            # done once


def test_price_at_uses_nearest_candle_and_failures_retry_then_stop():
    assert sv.price_at([_c("09:29", 10), _c("09:33", 12)], sv._ts("2026-11-11T09:31:00+05:30")) == 12
    assert sv.price_at([_c("09:00", 10)], sv._ts("2026-11-11T09:31:00+05:30")) is None
    t = _trade()
    for i in range(sv.MAX_TRIES):
        assert sv.attach_all([t], lambda tr: None, lambda k, s: []) == 0
        assert t["sell_view"]["tries"] == i + 1
    assert not sv.eligible(t)


def test_pnl_one_lot_gross_and_net():
    t = _trade(sell_view={"entry": 50.0, "exit": 40.0, "pct": 20.0})
    gross, net = sv.pnl(t, 75, 1.5)
    assert gross == 750 and net == round(750 - 50 * 75 * 0.015)
    assert sv.pnl(_trade(), 75, 1.5) == (None, None)


def test_nse_and_mcx_lookups():
    rows = [{"underlying_symbol": "NIFTY", "expiry": "2026-11-13", "strike_price": 24000, "option_type": "PE",
             "instrument_key": "NSE_FO|1"},
            {"underlying_symbol": "NIFTY", "expiry": "2026-11-13", "strike_price": 24000, "option_type": "CE",
             "instrument_key": "NSE_FO|2"}]
    fn = sv.nse_opp_key_fn(rows)
    assert fn(_trade(direction="CE")) == "NSE_FO|1" and fn(_trade(direction="PE")) == "NSE_FO|2"
    mrows = [{"exch_seg": "MCX", "name": "GOLD", "instrumenttype": "OPTFUT", "expiry": "27NOV2026",
              "symbol": "GOLD26NOV140000PE", "strike": "14000000.000000", "token": "555"}]
    mt = _trade(symbol="GOLD", direction="CE", strike=140000.0, expiry="2026-11-27")
    assert sv.mcx_opp_key_fn(mrows)(mt) == "MCX|555"
    assert sv.mcx_opp_key_fn(mrows)(dict(mt, direction="PE")) is None


def test_reports_and_ledger_carry_sell_side():
    win = _trade("T2_HIT", trade_id="a", tracked_entry_price=100.0, target_2=140.0,
                 sell_view={"option": "PE 24000.0", "entry": 50.0, "exit": 30.0, "pct": 40.0})
    loss = _trade("SL_HIT", trade_id="b", sell_view={"option": "PE 24000.0", "entry": 50.0, "exit": 70.0, "pct": -40.0})
    loss["alert_time"] = "2026-11-11T10:30:00+05:30"
    st = {"date": "2026-11-11", "shadow_trades": {"a": win, "b": loss}}
    rows = er.collect(st, {"NIFTY": 75})
    bvs = er.buy_vs_sell_rows(rows)
    tot = bvs[-1]
    assert tot["label"] == "TOTAL" and tot["n"] == 2 and tot["buy_wins"] == 1 and tot["sell_wins"] == 1
    assert tot["sell_net"] == round((20 * 75 - 50 * 75 * 0.015) + (-20 * 75 - 50 * 75 * 0.015))
    with tempfile.TemporaryDirectory() as d:
        import eod_image
        assert eod_image.render(st, "2026-11-11", os.path.join(d, "e.png"), {"NIFTY": 75})
        path = os.path.join(d, "o.csv")
        assert ol.append_resolved(st, path) == 2
        import csv
        r = {x["trade_id"]: x for x in csv.DictReader(open(path))}
        assert r["a"]["sell_result_pct"] == "40.0" and r["a"]["sell_entry"] == "50.0"
