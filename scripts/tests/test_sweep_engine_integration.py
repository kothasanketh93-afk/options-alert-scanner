"""Mocked end-to-end run of the sweep engine (no network): detection -> option leg -> setup dict -> main creates shadow trade."""
import os, sys
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config
from app.engines.sweep_reversal import engine as E


def _c(i, o, h, l, cl):
    hh, mm = divmod(9 * 60 + 15 + 5 * i, 60)
    return {"timestamp": f"2026-11-04T{hh:02d}:{mm:02d}:00+05:30", "open": o, "high": h, "low": l, "close": cl}


CANDLES = [_c(0, 105, 107, 103, 104), _c(1, 104, 106, 102, 103), _c(2, 103, 105, 101.5, 102), _c(3, 102, 103, 101, 102),
           _c(4, 101, 104, 99, 103.5)]
CHAIN = [{"strike_price": 100.0, "call_instrument_key": "K1", "call_ltp": 50.0, "call_oi": 5000, "call_volume": 9000,
          "call_bid": 49.9, "call_ask": 50.1, "call_delta": 0.5, "put_instrument_key": "K2", "put_ltp": 40.0,
          "put_oi": 6000, "put_volume": 9000, "put_bid": 39.9, "put_ask": 40.1, "put_delta": -0.5}]


def test_engine_builds_sweep_setup(monkeypatch=None):
    import pytest
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(E, "current_session", lambda: "MORNING")
        mp.setattr(E, "resolve_underlying_key", lambda s: "NSE_INDEX|X")
        mp.setattr(E, "get_atm_strike", lambda s: (100.0, 103.5))
        mp.setattr(E, "current_expiry", lambda s: "2026-11-10")
        mp.setattr(E, "liquidity_ok", lambda *a, **k: True)
        mp.setattr(E.market_data, "get_recent_daily_candles", lambda k, n: [{"high": 120.0, "low": 100.0, "close": 110.0}] * 6)
        mp.setattr(E.market_data, "get_recent_underlying_candles", lambda k, i, n: CANDLES)
        mp.setattr(E.market_data, "get_atr_warmup_candles", lambda *a, **k: (CANDLES * 4, None))
        mp.setattr(E.market_data, "get_option_chain", lambda k, e: CHAIN)
        mp.setattr(config, "SWEEP_SYMBOLS", ["NIFTY"])
        state = {}
        out = E.detect_all_sweep_setups(state, 9 * 60 + 40)
        names = {s["strategy"] for s in out}
        assert "sweep" in names
        s = next(x for x in out if x["strategy"] == "sweep")
        assert s["direction"] == "CE" and s["stop_loss"] < s["alert_premium"] < s["target_1"] < s["target_2"]
        assert s["sweep_extreme"] == 99 and s["instrument_key"] == "K1"
        assert state["flow_snap"]["NIFTY"][0]["call_oi"] == 5000   # snapshot stored for the OI-flow proxy
    finally:
        mp.undo()
