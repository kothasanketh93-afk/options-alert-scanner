import rfp
import tracker


def mk(i, o, h, l, c, v=100):
    mins = 9 * 60 + 15 + 5 * i
    return {"timestamp": f"2026-11-26T{mins // 60:02d}:{mins % 60:02d}:00+05:30", "open": o, "high": h, "low": l, "close": c, "volume": v}


def uptrend(n=12, start=100.0, step=1.0):
    out, p = [], start
    for i in range(n):
        out.append(mk(i, p, p + step + 0.2, p - 0.2, p + step))
        p += step
    return out


def test_regime_up_down_chop():
    up = uptrend()
    assert rfp.regime(up, atr=1.0)["regime"] == "UP"
    down = [dict(c, open=-c["open"], high=-c["low"], low=-c["high"], close=-c["close"]) for c in up]
    assert rfp.regime(down, atr=1.0)["regime"] == "DOWN"
    flat = [mk(i, 100, 100.4, 99.6, 100 + (0.1 if i % 2 else -0.1)) for i in range(12)]
    assert rfp.regime(flat, atr=1.0)["regime"] == "CHOP"


def test_regime_needs_data():
    assert rfp.regime(uptrend(4), 1.0)["regime"] == "NONE"
    assert rfp.regime(uptrend(12), 0)["regime"] == "NONE"


def test_detect_chop_day_gives_no_trade():
    flat = [mk(i, 100, 100.4, 99.6, 100 + (0.1 if i % 2 else -0.1)) for i in range(40)]
    assert rfp.detect("BANKNIFTY", flat, flat, 1.0) is None


def test_detect_unknown_symbol_none():
    assert rfp.detect("GOLD", uptrend(), uptrend(), 1.0) is None


def test_pe_needs_full_score_for_nifty(monkeypatch):
    up = uptrend()
    mirror = [dict(c, open=-c["open"], high=-c["low"], low=-c["high"], close=-c["close"]) for c in up]
    monkeypatch.setattr(rfp, "regime", lambda t, a: {"regime": "DOWN", "score": -2, "votes": {}})
    monkeypatch.setattr(rfp.base, "detect_ema_pullback", lambda *a, **k: {"direction": "PE", "level_name": "EMA9", "level_value": 1,
                                                                          "extreme": 2, "signal_close": 1, "signal_index": 1})
    assert rfp.detect("NIFTY", mirror, mirror, 1.0) is None            # NIFTY PE requires 3/3 votes
    sig = rfp.detect("BANKNIFTY", mirror, mirror, 1.0)                  # BANKNIFTY accepts 2/3
    assert sig and sig["direction"] == "PE" and sig["be_trigger_pct"] == 8.0 and sig["regime"] == "DOWN"


def test_direction_must_match_regime(monkeypatch):
    monkeypatch.setattr(rfp, "regime", lambda t, a: {"regime": "UP", "score": 3, "votes": {}})
    monkeypatch.setattr(rfp.base, "detect_ema_pullback", lambda *a, **k: {"direction": "PE", "level_name": "x", "level_value": 1,
                                                                          "extreme": 2, "signal_close": 1, "signal_index": 1})
    assert rfp.detect("BANKNIFTY", uptrend(), uptrend(), 1.0) is None


def test_per_trade_breakeven_moves_stop_once():
    t = {"tracked_entry_price": 100.0, "stop_loss": 90.0, "max_favorable_pct": 8.5, "be_trigger_pct": 8.0}
    ev = tracker._maybe_apply_trailing_stop(t, __import__("datetime").datetime.now())
    assert ev and t["stop_loss"] == 100.0
    assert tracker._maybe_apply_trailing_stop(t, __import__("datetime").datetime.now()) is None


def test_breakeven_not_before_trigger_and_global_off():
    t = {"tracked_entry_price": 100.0, "stop_loss": 90.0, "max_favorable_pct": 5.0, "be_trigger_pct": 8.0}
    assert tracker._maybe_apply_trailing_stop(t, __import__("datetime").datetime.now()) is None
    t2 = {"tracked_entry_price": 100.0, "stop_loss": 90.0, "max_favorable_pct": 50.0}      # no per-trade BE, global trailing disabled
    assert tracker._maybe_apply_trailing_stop(t2, __import__("datetime").datetime.now()) is None
    assert t2["stop_loss"] == 90.0
