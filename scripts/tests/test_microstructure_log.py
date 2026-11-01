import csv
from datetime import datetime

import microstructure_log as m


def _row(k, spot=24000.0, **kw):
    r = {"strike_price": k, "underlying_spot_price": spot,
         "call_instrument_key": f"C{k}", "call_ltp": 100.0, "call_bid": 99.0, "call_ask": 101.0,
         "call_bid_qty": 650, "call_ask_qty": 300, "call_volume": 1000, "call_oi": 5000,
         "call_prev_oi": 4000, "call_delta": 0.5, "call_gamma": 0.001, "call_theta": -9.0,
         "call_vega": 8.0, "call_iv": 14.0,
         "put_instrument_key": f"P{k}", "put_ltp": 90.0, "put_bid": 88.0, "put_ask": None,
         "put_delta": -0.5}
    r.update(kw)
    return r


CHAIN = [_row(k) for k in range(23800, 24250, 50)]
NOW = datetime(2026, 11, 3, 10, 5, 0)


def test_atm_window_and_fields():
    rows = m.build_rows("NIFTY", "2026-11-03", 0, CHAIN, NOW, width=2)
    assert len(rows) == 10          # 5 strikes x CE/PE
    ce = [r for r in rows if r["side"] == "CE" and r["atm_offset"] == 0][0]
    assert ce["strike"] == 24000 and ce["mid"] == 100.0 and ce["spread_abs"] == 2.0
    assert ce["spread_pct"] == 2.0 and ce["bid_qty"] == 650 and ce["theta"] == -9.0
    assert ce["intrinsic"] == 0.0 and ce["minute"] == 605


def test_missing_ask_is_not_zero_spread():
    rows = m.build_rows("NIFTY", "2026-11-03", 0, CHAIN, NOW, width=0)
    pe = [r for r in rows if r["side"] == "PE"][0]
    assert pe["ask"] is None and pe["spread_abs"] is None and pe["spread_pct"] is None


def test_tracked_contract_always_logged_with_trade_id():
    rows = m.build_rows("NIFTY", "2026-11-03", 0, CHAIN, NOW, width=0, trade_map={"C23800": ["T1"]})
    far = [r for r in rows if r["instrument_key"] == "C23800"]
    assert len(far) == 1 and far[0]["trade_ids"] == "T1" and far[0]["intrinsic"] == 200.0


def test_open_trade_map_skips_terminal():
    state = {"orb_pivot_shadow_trades": {
        "A": {"instrument_key": "C1", "status": "ACTIVE"},
        "B": {"instrument_key": "C2", "status": "SL_HIT"}}}
    assert m.open_trade_map(state) == {"C1": ["A"]}


def test_due_window_and_gap():
    st = {}
    assert not m.due(st, datetime(2026, 11, 3, 9, 10))
    assert not m.due(st, datetime(2026, 11, 3, 15, 40))
    assert m.due(st, datetime(2026, 11, 3, 9, 30))
    m.mark_done(st, datetime(2026, 11, 3, 9, 30))
    assert not m.due(st, datetime(2026, 11, 3, 9, 32))
    assert m.due(st, datetime(2026, 11, 3, 9, 35))
    assert m.due(st, datetime(2026, 11, 4, 9, 31))


def test_append_writes_header_once(tmp_path):
    rows = m.build_rows("NIFTY", "2026-11-03", 0, CHAIN, NOW, width=0)
    p = tmp_path / "d" / "x.csv"
    assert m.append_rows(rows, p) == 2
    assert m.append_rows(rows, p) == 2
    lines = list(csv.reader(open(p)))
    assert lines[0] == m.COLUMNS and len(lines) == 5
