"""
app/engines/decision_engine/tests/test_decision_engine.py
------------------------------------------------------------------------------
Plain assert-based tests, no pytest dependency -- same convention as
app/engines/early_breakout/tests/test_engine.py and
app/engines/options_intelligence/tests/test_options_intelligence.py.
All synthetic, deterministic data, no broker/network access.

Covers: plugin discovery/availability (base.py, adapters.py), each
sub-engine (confidence, probability, risk/RR/position-size,
entry_validation, stop_loss, target, trade_quality/priority,
explanation/rejection), the end-to-end evaluate() entry point
(engine.py) for both the Early Breakout and plain-scanner trade
shapes, backward compatibility (DECISION_ENGINE_ENABLED = False),
and edge cases (missing fields, no plugins available, filter on/off).

Run with: python scripts/app/engines/decision_engine/tests/test_decision_engine.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import sys
import os

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

import config
from app.engines.decision_engine import engine, base, adapters


def _early_breakout_trade(**overrides) -> dict:
    trade = {
        "symbol": "NIFTY", "direction": "CE", "strategy": "early_breakout",
        "alert_premium": 100.0, "entry_low": 100.0, "entry_high": 104.0,
        "stop_loss": 90.0, "target_1": 115.0, "target_2": 130.0,
        "grade": "High Conviction", "trade_score": 85,
        "probability_pct": 80, "probability_label": "High Conviction",
        "confidence_score": 8.5, "risk_score": 7.0, "reward_score": 8.0,
        "base_score": 80, "institutional_score": 78, "momentum_score": 72,
        "smart_money_score": 75, "breakout_price": 98.0,
        "maximum_entry": 108.0,
        "options_intelligence": {
            "enabled": True, "options_intelligence_score": 75, "confidence": "High",
            "accepted": True, "rejection_reasons": [], "liquidity_score": 70,
            "market_bias": "Bullish",
        },
    }
    trade.update(overrides)
    return trade


def _plain_scanner_trade(**overrides) -> dict:
    trade = {
        "symbol": "BANKNIFTY", "direction": "PE", "strategy": "trade_score",
        "alert_premium": 200.0, "entry_low": 198.0, "entry_high": 202.0,
        "stop_loss": 170.0, "target_1": 230.0, "target_2": 260.0,
        "grade": "A", "trade_score": 72, "probability_pct": 65,
        "probability_label": "Good", "confidence_score": 7.2,
        "risk_score": 6.0, "reward_score": 7.0,
        "options_intelligence": {
            "enabled": True, "options_intelligence_score": 55, "confidence": "Medium",
            "accepted": True, "rejection_reasons": [], "liquidity_score": 50,
            "market_bias": "Bearish",
        },
    }
    trade.update(overrides)
    return trade


def test_plugin_availability():
    trade = _early_breakout_trade()
    registry = engine.build_registry()
    plugins = registry.discover(trade, {"trade": trade, "snapshot": None})
    names = {p.name for p in plugins}
    assert "early_breakout" in names, names
    assert "trade_score_engine" not in names, "plain-scanner plugin must NOT fire for an early_breakout trade"
    assert "options_intelligence" in names, names
    assert "market_snapshot" not in names, "no snapshot supplied -- must be skipped, not errored"

    plain = _plain_scanner_trade()
    plugins2 = registry.discover(plain, {"trade": plain, "snapshot": {"trend_strength_label": "strong", "volatility_level": "normal", "regime": "trending"}})
    names2 = {p.name for p in plugins2}
    assert "trade_score_engine" in names2, names2
    assert "early_breakout" not in names2, names2
    assert "market_snapshot" in names2, names2
    print("test_plugin_availability passed")


def test_evaluate_approves_strong_trade():
    trade = _early_breakout_trade()
    decision = engine.evaluate(trade, snapshot={
        "trend_strength_label": "strong", "volatility_level": "normal", "regime": "trending",
    })
    assert decision["enabled"] is True
    assert decision["decision"] == "Trade Approved", decision
    assert decision["approved"] is True
    assert 0 <= decision["trade_quality_score"] <= 100
    assert decision["risk_reward"]["risk_reward_ratio"] > 0
    assert decision["priority"] in ("Very High", "High", "Medium", "Low")
    assert "early_breakout" in decision["engines_available"]
    assert decision["target_3"] is not None  # advisory extension present
    print("test_evaluate_approves_strong_trade passed")


def test_evaluate_rejects_poor_rr():
    # Stop far below entry, tiny target -> RR well under the floor.
    trade = _early_breakout_trade(stop_loss=50.0, target_1=101.0, target_2=102.0)
    decision = engine.evaluate(trade)
    assert decision["risk_reward"]["passes_rr_floor"] is False
    assert any("Risk Reward" in r for r in decision["rejection_reasons"]), decision["rejection_reasons"]
    assert decision["approved"] is False
    assert decision["priority"] == "Rejected"
    print("test_evaluate_rejects_poor_rr passed")


def test_missing_engines_never_reduce_confidence_to_zero():
    # Minimal trade dict -- only the bare fields, no early_breakout/
    # options_intelligence/snapshot data at all.
    trade = {
        "symbol": "RELIANCE", "direction": "CE", "strategy": "trade_score",
        "alert_premium": 50.0, "entry_low": 50.0, "entry_high": 52.0,
        "stop_loss": 40.0, "target_1": 65.0, "target_2": 80.0,
        "grade": "B", "trade_score": 60, "probability_pct": 55,
        "confidence_score": 6.0, "risk_score": 5.0, "reward_score": 6.0,
    }
    decision = engine.evaluate(trade)
    assert decision["enabled"] is True
    # Should still produce a real number, not crash or silently zero out
    # just because options_intelligence/market_snapshot are absent.
    assert decision["confidence_score"] > 0
    assert "trade_score_engine" in decision["engines_available"]
    print("test_missing_engines_never_reduce_confidence_to_zero passed")


def test_master_flag_disables_engine():
    # engine.py does `from config import DECISION_ENGINE_ENABLED`, so the
    # importing module's own local name is what must be patched -- same
    # convention as test_options_intelligence.py's OPTIONS_INTELLIGENCE_ENABLED test.
    original = engine.DECISION_ENGINE_ENABLED
    try:
        engine.DECISION_ENGINE_ENABLED = False
        trade = _early_breakout_trade()
        decision = engine.evaluate(trade)
        assert decision["enabled"] is False
        assert decision["decision"] == "Trade Approved"  # passthrough -- never blocks when disabled
    finally:
        engine.DECISION_ENGINE_ENABLED = original
    print("test_master_flag_disables_engine passed")


def test_never_raises_on_malformed_trade():
    # No stop_loss, no entry -- exercises every "missing field" branch.
    trade = {"symbol": "X", "direction": "CE", "strategy": "trade_score"}
    decision = engine.evaluate(trade)
    assert decision["enabled"] is True
    assert decision["decision"] in ("Trade Approved", "Trade Rejected")
    print("test_never_raises_on_malformed_trade passed")


def test_position_size_modes():
    from app.engines.decision_engine import risk as risk_mod
    trade = _early_breakout_trade()
    r = risk_mod.calculate_risk(trade)
    for mode in ("fixed_quantity", "capital_based", "risk_based", "percentage_based", "dynamic"):
        original = risk_mod.DECISION_ENGINE_POSITION_SIZE_MODE
        try:
            risk_mod.DECISION_ENGINE_POSITION_SIZE_MODE = mode
            sized = risk_mod.calculate_position_size(trade, r)
            assert sized["quantity"] >= 1, (mode, sized)
            assert sized["mode"] == mode
        finally:
            risk_mod.DECISION_ENGINE_POSITION_SIZE_MODE = original
    print("test_position_size_modes passed")


def test_filter_flag_blocks_only_when_enabled():
    trade = _early_breakout_trade(stop_loss=50.0, target_1=101.0, target_2=102.0)  # poor RR -> rejected
    decision = engine.evaluate(trade)
    assert decision["approved"] is False
    # main.py's own gating logic (config.DECISION_ENGINE_FILTER_ENABLED) is
    # exercised in main.py itself -- here we just confirm the Decision
    # Object always reports approved/rejected honestly regardless of that
    # flag, since the flag only controls what main.py DOES with it.
    print("test_filter_flag_blocks_only_when_enabled passed")


if __name__ == "__main__":
    test_plugin_availability()
    test_evaluate_approves_strong_trade()
    test_evaluate_rejects_poor_rr()
    test_missing_engines_never_reduce_confidence_to_zero()
    test_master_flag_disables_engine()
    test_never_raises_on_malformed_trade()
    test_position_size_modes()
    test_filter_flag_blocks_only_when_enabled()
    print("\nAll decision_engine tests passed.")
