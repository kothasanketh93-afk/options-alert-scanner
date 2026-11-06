import os, sys
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import mcx_main, tracker


def _row(name, kind, strike, exp="17Nov2026", itype="OPTFUT", tok="1"):
    return {"exch_seg": "MCX", "name": name, "instrumenttype": itype, "expiry": exp, "strike": str(strike * 100.0),
            "symbol": f"{name}{exp}{strike}{kind}", "token": tok, "lotsize": "100"}


def test_resolve_contracts_skips_expiry_day_and_picks_nearest():
    m = [_row("CRUDEOIL", "CE", 8500, "17Nov2026", tok="a"), _row("CRUDEOIL", "CE", 8500, "16Dec2026", tok="b"),
         _row("CRUDEOIL", "", 0, "19Nov2026", "FUTCOM", "f")]
    now = datetime(2026, 11, 17, 10, 0)
    c = mcx_main.resolve_contracts(m, "CRUDEOIL", now)           # 17 Nov is expiry day -> next expiry
    assert c["expiry"] == "2026-12-16" and c["chain"][0]["_strike"] == 8500.0 and c["fut"]["token"] == "f"
    assert mcx_main.resolve_contracts(m, "GOLD", now) is None


def test_session_window():
    assert mcx_main.in_session(datetime(2026, 11, 5, 21, 0)) and not mcx_main.in_session(datetime(2026, 11, 7, 12, 0))
    assert mcx_main.in_session(datetime(2026, 11, 5, 23, 45)) and not mcx_main.in_session(datetime(2026, 11, 5, 8, 30))


def test_tracker_uses_trade_specific_close():
    from datetime import timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    dt = datetime(2026, 11, 5, 20, 0, tzinfo=ist)
    assert tracker._is_after_market_close(dt)                      # NSE default: after 15:30
    assert not tracker._is_after_market_close(dt, (23, 25))        # MCX trade: still open
    assert tracker._is_after_market_close(datetime(2026, 11, 5, 23, 26, tzinfo=ist), (23, 25))


def test_summary_text_without_trades():
    assert "no closed trades" in mcx_main.summary_text({"date": "2026-11-05", "mcx_shadow_trades": {}, "rejects": {}})


def test_mcx_report_week_and_empty_day():
    import csv, tempfile
    import mcx_report
    from outcomes_ledger import COLUMNS
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "o.csv")
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerow(dict(trade_id="1", date="2026-11-05", engine="mcx", strategy="fvg", symbol="GOLDM", direction="PE", strike="144000",
                            alert_time="2026-11-05T18:10:00", entry_price="2000", final_status="T2_HIT", t1_hit="True", result_pct="10", target_1="2100"))
        data = mcx_report.collect_week(p, "2026-11-05")
        assert len(data["trades"]) == 1 and round(data["trades"][0]["gross"]) == 2000 and data["trades"][0]["bucket"] == "1700-2100"
        assert mcx_report.render_weekly(p, "2026-11-05", os.path.join(tmp, "w.png"))
        assert not mcx_report.render_daily({"date": "2026-11-05", "mcx_shadow_trades": {}}, os.path.join(tmp, "d.png"))


class _FakeBroker:
    def __init__(self, quote, candles):
        self.quote, self.candles = quote, candles

    def _batch_quotes(self, exchange, tokens):
        return {t: self.quote for t in tokens}

    def get_candles(self, key, interval, since_iso=None):
        return [c for c in self.candles if c["timestamp"] > (since_iso or "")]


def test_mcx_create_track_and_ledger(monkeypatch, tmp_path):
    import outcomes_ledger
    sent = []
    monkeypatch.setattr(mcx_main.tg, "send", lambda text: sent.append(text))
    now = datetime(2026, 11, 5, 21, 44, tzinfo=mcx_main.IST)
    chain = [_row("CRUDEOIL", k, 8500, tok=f"t{k}") for k in ("CE", "PE")]
    for x in chain:
        x["_strike"] = 8500.0
    c = {"fut": {"token": "f"}, "chain": chain, "expiry": "2026-11-17", "lot": 100}
    quote = {"ltp": 400.0, "depth": {"buy": [{"price": 399.5}], "sell": [{"price": 400.5}]}, "opnInterest": 1000, "tradeVolume": 500}
    st = {"date": "2026-11-05", "mcx_shadow_trades": {}, "next_seq": {}, "flow_snap": {}, "rejects": {}}
    broker = _FakeBroker(quote, [])
    sig = {"direction": "CE", "level_name": "PDL", "signal_close": 8500.0, "extreme": 8450.0, "level_value": 8460.0}
    mcx_main.create_trade(broker, st, "CRUDEOIL", c, "sweep", sig, 40.0, None, None, {"tCE": quote}, now)
    assert len(st["mcx_shadow_trades"]) == 1 and sent and "MCX SHADOW" in sent[0]
    t = next(iter(st["mcx_shadow_trades"].values()))
    assert t["market_close_hhmm"] == [23, 25] and t["lot_multiplier"] == 100 and t["stop_loss"] < t["alert_premium"] < t["target_1"]
    # price falls through the stop on the next minute candles -> SL_HIT, one-lot loss reported
    sl = t["stop_loss"]
    candles = [{"timestamp": "2026-11-05T21:45:00+05:30", "open": 400, "high": 401, "low": 399, "close": 400, "volume": 1},
               {"timestamp": "2026-11-05T21:46:00+05:30", "open": 400, "high": 400, "low": sl - 5, "close": sl - 4, "volume": 1}]
    broker.candles = candles
    mcx_main.track_open(broker, st)
    assert t["status"] == "SL_HIT"
    assert any("Stop-loss hit" in s for s in sent)
    ledger = tmp_path / "o.csv"
    monkeypatch.setattr(outcomes_ledger, "STATE_KEYS", outcomes_ledger.STATE_KEYS + (("mcx_shadow_trades", "mcx"),))
    assert outcomes_ledger.append_resolved(st, ledger) == 1


def test_mcx_rejects_wide_spread(monkeypatch):
    monkeypatch.setattr(mcx_main.tg, "send", lambda text: None)
    now = datetime(2026, 11, 5, 21, 44, tzinfo=mcx_main.IST)
    chain = [_row("GOLD", k, 144000, tok=f"t{k}") for k in ("CE", "PE")]
    for x in chain:
        x["_strike"] = 144000.0
    c = {"fut": {"token": "f"}, "chain": chain, "expiry": "2026-11-27", "lot": 1}
    wide = {"ltp": 3000.0, "depth": {"buy": [{"price": 2800.0}], "sell": [{"price": 3200.0}]}, "opnInterest": 5, "tradeVolume": 100}
    st = {"date": "2026-11-05", "mcx_shadow_trades": {}, "next_seq": {}, "flow_snap": {}, "rejects": {}}
    sig = {"direction": "CE", "level_name": "PDL", "signal_close": 144000.0, "extreme": 143500.0, "level_value": 143600.0}
    mcx_main.create_trade(_FakeBroker(wide, []), st, "GOLD", c, "sweep", sig, 300.0, None, None, {"tCE": wide}, now)
    assert not st["mcx_shadow_trades"] and any(k.startswith("GOLD:spread_") for k in st["rejects"])


def test_prune_dated_files(tmp_path, monkeypatch):
    import state as state_mod
    monkeypatch.setattr(state_mod, "STATE_CLONE_DIR", tmp_path)
    d = tmp_path / "instrument_master"
    d.mkdir()
    for name in ("2026-10-01.json.gz", "2026-10-25.json.gz", "2026-11-05.json.gz", "notes.txt"):
        (d / name).write_text("x")
    assert state_mod.prune_dated_files("instrument_master", 14, today="2026-11-05") == 1
    assert sorted(p.name for p in d.iterdir()) == ["2026-10-25.json.gz", "2026-11-05.json.gz", "notes.txt"]


def test_pick_signal_future_uses_open_interest_and_sticks(monkeypatch):
    futs = [{"token": "a", "symbol": "GOLDM04DEC26FUT"}, {"token": "b", "symbol": "GOLDM05JAN27FUT"}, {"token": "c", "symbol": "GOLDM05FEB27FUT"}]
    c = {"fut": futs[0], "futs": futs, "opt_fut": futs[0]}

    class B:
        calls = 0

        def _batch_quotes(self, ex, toks):
            B.calls += 1
            return {"a": {"opnInterest": 120}, "b": {"opnInterest": 90000}, "c": {"opnInterest": 3000}}

    st = {}
    assert mcx_main.pick_signal_future(B(), st, "GOLDM", c)["token"] == "b"
    assert mcx_main.pick_signal_future(B(), st, "GOLDM", c)["token"] == "b" and B.calls == 1     # chosen once per day


def test_resolve_contracts_option_underlying_is_first_future_on_or_after_option_expiry():
    m = [_row("GOLDM", "CE", 144000, "27Nov2026", tok="o"), _row("GOLDM", "", 0, "05Nov2026", "FUTCOM", "f0"),
         _row("GOLDM", "", 0, "04Dec2026", "FUTCOM", "f1"), _row("GOLDM", "", 0, "05Jan2027", "FUTCOM", "f2")]
    c = mcx_main.resolve_contracts(m, "GOLDM", datetime(2026, 11, 6, 10, 0))
    assert c["opt_fut"]["token"] == "f1" and [f["token"] for f in c["futs"]] == ["f1", "f2"]
