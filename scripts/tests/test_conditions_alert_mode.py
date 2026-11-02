"""CONDITIONS_ALERT_MODE: a setup that clears every gate becomes a live candidate WITHOUT a calibrated
probability or a trade_score floor; ordering ignores trade_score; Telegram shows no score/probability."""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))
sys.path.insert(0, _here)

import test_scanner_to_alert_pipeline as pipeline
import test_probability_calibration_pipeline as calib
import scanner
import ranking
import state as state_mod
import telegram_notify as tg
from datetime import datetime


def _score(mode, grade=None):
    o = (scanner.config.CONDITIONS_ALERT_MODE, scanner.config.SCORE_GATE_ENABLED, scanner.trade_score.compute_grade)
    scanner.config.CONDITIONS_ALERT_MODE, scanner.config.SCORE_GATE_ENABLED = mode, False
    if grade:
        scanner.trade_score.compute_grade = lambda s: grade
    try:
        return pipeline._run_mocked_score_strike(**calib._valid_kwargs(calibrated_probability_override=None))
    finally:
        scanner.config.CONDITIONS_ALERT_MODE, scanner.config.SCORE_GATE_ENABLED, scanner.trade_score.compute_grade = o


def test_conditions_met_returns_live_candidate_without_probability():
    r = _score(True)
    assert r is not None and r["probability_pct"] is None and "unvalidated" in r["probability_label"].lower()
    assert "_conditions_rank" in r


def test_low_score_still_alerts_in_conditions_mode():
    r = _score(True, grade="Reject")
    assert r is not None


def test_legacy_mode_still_rejects_without_probability():
    assert _score(False) is None


def test_ranking_ignores_trade_score(monkeypatch):
    monkeypatch.setattr(ranking.__dict__.get("config", None) or __import__("config"), "CONDITIONS_ALERT_MODE", True)
    a = {"symbol": "A", "grade": "A+", "_quality_score": 95, "_conditions_rank": (2, 1.0)}
    b = {"symbol": "B", "grade": "B", "_quality_score": 40, "_conditions_rank": (4, 1.5)}
    assert [x["symbol"] for x in ranking.rank_all([a, b])] == ["B", "A"]


def test_probability_none_is_not_recorded():
    st = {}
    state_mod.record_probability(st, "NIFTY", "CE", None, datetime(2026, 11, 3, 10, 0))
    assert st.get("probability_history", {}) == {}


def test_telegram_alert_has_no_score_or_probability():
    r = _score(True)
    r.update(display_label="Trade 1", trade_id="T", alternatives=[])
    text = tg.fmt_new_alert(r)
    assert "CONDITIONS MET" in text and "Trade Score" not in text and "Probability :" not in text and "Grade" not in text


def test_limit_price_line_in_alert():
    r = _score(True)
    r.update(display_label="Trade 1", trade_id="T", alternatives=[], bid=99.0, ask=100.0, spread_pct=1.0)
    text = tg.fmt_new_alert(r)
    assert "LIMIT buy at ₹100.0" in text and "do not chase above" in text


def test_spread_caps_index_vs_stock():
    import filters
    # index premium 100: spread 2% > 1% cap -> rejected; 0.5% -> ok (large OI/volume, symbol=None => index)
    assert filters.liquidity_ok(10**9, 10**9, 99.0, 101.0, 100.0, None) is False
    assert filters.liquidity_ok(10**9, 10**9, 99.75, 100.25, 100.0, None) is True
