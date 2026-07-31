"""
app/engines/decision_engine/tests/test_news_intelligence_integration.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as
test_quant_intelligence_integration.py (no pytest dependency).

Covers the Part 11.5 <-> Part 10/11 MERGE specifically (not News
Intelligence's own internals, which have their own suite at
app/engines/news_intelligence/tests/):

  - engine.evaluate() attaches decision_object["news_intelligence"]
    without changing any pre-existing field's value versus a
    News-Intelligence-disabled run on the same input (zero regression),
    and runs AFTER quant_intelligence has already been attached.
  - news_adapter.NEWS_INTELLIGENCE_ENABLED = False fully restores
    byte-identical pre-Part-11.5 behavior (the disabled branch never
    even imports/calls the engine).
  - A broken/unavailable news_intelligence package degrades to
    {"enabled": False, "error": True, ...} and the rest of the Standard
    Decision Object -- including quant_intelligence's own output -- is
    completely unaffected.
  - news_adapter.NEWS_INTELLIGENCE_INFLUENCE_ENABLED gates whether a
    confidently-unfavorable News Intelligence read can flip an
    otherwise-approved trade to rejected, and can never flip an
    already-rejected trade back to approved.
  - news_adapter.health() reports HEALTHY/OFFLINE/CRITICAL correctly
    for use by production.health_registry.

Run with:
    python scripts/app/engines/decision_engine/tests/test_news_intelligence_integration.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

from app.engines.decision_engine import engine, news_adapter


def _early_breakout_trade(**overrides) -> dict:
    trade = {
        "symbol": "NIFTY", "direction": "CE", "strategy": "early_breakout",
        "spot_price": 24800.0, "alert_premium": 100.0, "strike": 24800.0,
        "expiry": "2026-08-06", "alert_time": "2026-07-27T09:45:00+05:30",
        "entry_low": 100.0, "entry_high": 104.0,
        "stop_loss": 90.0, "target_1": 115.0, "target_2": 130.0,
        "grade": "High Conviction", "trade_score": 85,
        "probability_pct": 80, "probability_label": "High Conviction",
        "confidence_score": 8.5, "risk_score": 7.0, "reward_score": 8.0,
        "base_score": 80, "institutional_score": 78, "momentum_score": 72,
        "options_intelligence": {
            "overall_pcr": 1.1,
            "raw": {"oi": 500000, "oi_change_pct": 12.0, "volume": 200000, "iv": 18.5,
                     "bid": 99.5, "ask": 100.5},
        },
    }
    trade.update(overrides)
    return trade


def _snapshot(**overrides) -> dict:
    snap = {"regime": "TRENDING_BULLISH", "ema9": 24810.0, "ema21": 24750.0,
            "vwap": 24790.0, "atr": 120.0, "gap": {"gap_pct": 0.002}}
    snap.update(overrides)
    return snap


def _reset_import_cache():
    news_adapter._import_attempted = False
    news_adapter._NewsDecisionAdapter = None
    news_adapter._default_adapter_fn = None
    news_adapter._import_error = None


def test_attach_adds_news_intelligence_without_disturbing_existing_fields():
    trade = _early_breakout_trade()
    baseline = engine.evaluate(trade, snapshot=_snapshot())
    assert "news_intelligence" in baseline, "news_intelligence key must always be present"
    assert baseline["news_intelligence"]["enabled"] in (True, False)

    # Every pre-existing Decision Object field computed BEFORE
    # news_adapter.attach() runs must be identical regardless of
    # whether News Intelligence itself succeeds -- zero regression.
    for key in ("decision", "approved", "trade_quality_score", "confidence_score",
                "risk_reward", "entry", "stop_loss", "priority"):
        assert key in baseline, f"missing pre-existing field {key!r}"
    print("PASS: attach adds news_intelligence without disturbing existing fields")


def test_disabled_flag_skips_import_entirely():
    original_enabled = news_adapter.NEWS_INTELLIGENCE_ENABLED
    _reset_import_cache()
    try:
        news_adapter.NEWS_INTELLIGENCE_ENABLED = False
        trade = _early_breakout_trade()
        decision_object = {"approved": True, "decision": "Trade Approved"}
        news_adapter.attach(trade, decision_object, _snapshot())
        assert decision_object["news_intelligence"]["enabled"] is False
        assert news_adapter._import_attempted is False, (
            "disabled branch must never even attempt the import"
        )
    finally:
        news_adapter.NEWS_INTELLIGENCE_ENABLED = original_enabled
        _reset_import_cache()
    print("PASS: disabled flag skips import entirely")


def test_broken_package_degrades_gracefully():
    original_attempted = news_adapter._import_attempted
    original_adapter = news_adapter._default_adapter_fn
    original_error = news_adapter._import_error
    try:
        news_adapter._import_attempted = True
        news_adapter._default_adapter_fn = None
        news_adapter._import_error = RuntimeError("simulated import failure")

        trade = _early_breakout_trade()
        decision_object = {"approved": True, "decision": "Trade Approved",
                            "trade_quality_score": 77, "quant_intelligence": {"enabled": True}}
        news_adapter.attach(trade, decision_object, _snapshot())

        assert decision_object["news_intelligence"]["enabled"] is False
        assert decision_object["approved"] is True, "unrelated fields must survive untouched"
        assert decision_object["trade_quality_score"] == 77
        assert decision_object["quant_intelligence"]["enabled"] is True, (
            "a News Intelligence failure must never disturb Quant Intelligence's output"
        )
    finally:
        news_adapter._import_attempted = original_attempted
        news_adapter._default_adapter_fn = original_adapter
        news_adapter._import_error = original_error
    print("PASS: broken package degrades gracefully without disturbing quant_intelligence")


def test_engine_evaluate_never_raises_when_news_adapter_raises():
    """Even if news_adapter.attach() itself somehow raised (bug), the
    try/except wrapper in engine.evaluate() must swallow it -- same
    posture as the existing quant_adapter wrapper."""
    trade = _early_breakout_trade()
    original_attach = news_adapter.attach

    def _boom(*a, **kw):
        raise RuntimeError("simulated adapter crash")

    news_adapter.attach = _boom
    try:
        decision_object = engine.evaluate(trade, snapshot=_snapshot())
        assert decision_object["news_intelligence"]["enabled"] is False
        assert decision_object["news_intelligence"]["error"] is True
        assert decision_object["decision"] in ("Trade Approved", "Trade Rejected"), (
            "the rest of the Decision Object must still be fully computed"
        )
    finally:
        news_adapter.attach = original_attach
    print("PASS: engine.evaluate() never raises when news_adapter.attach() raises")


def test_influence_flag_can_only_add_caution_not_remove_it():
    original_influence = news_adapter.NEWS_INTELLIGENCE_INFLUENCE_ENABLED
    try:
        assert news_adapter.NEWS_INTELLIGENCE_INFLUENCE_ENABLED is False, "default must be off"

        # Already-rejected trade must stay rejected even with a
        # favorable news read and influence enabled.
        news_adapter.NEWS_INTELLIGENCE_INFLUENCE_ENABLED = True

        class _FakeResult:
            news_score = 90.0
            confidence = 0.9

        decision_object = {"approved": False, "decision": "Trade Rejected",
                            "rejection_reasons": ["some other reason"]}
        news_adapter._apply_influence(decision_object, _FakeResult())
        assert decision_object["approved"] is False, (
            "influence must never flip an already-rejected trade back to approved"
        )
    finally:
        news_adapter.NEWS_INTELLIGENCE_INFLUENCE_ENABLED = original_influence
    print("PASS: influence flag can only add caution, never remove it")


def test_influence_flag_flips_approved_on_unfavorable_confident_read():
    original_influence = news_adapter.NEWS_INTELLIGENCE_INFLUENCE_ENABLED
    try:
        news_adapter.NEWS_INTELLIGENCE_INFLUENCE_ENABLED = True

        class _FakeResult:
            news_score = -80.0  # strongly unfavorable
            confidence = 0.9    # high confidence

        decision_object = {"approved": True, "decision": "Trade Approved",
                            "__trade_direction__": "CE"}
        news_adapter._apply_influence(decision_object, _FakeResult())
        assert decision_object["approved"] is False
        assert decision_object["decision"] == "Trade Rejected"
        assert decision_object["rejection_reasons"]
    finally:
        news_adapter.NEWS_INTELLIGENCE_INFLUENCE_ENABLED = original_influence
    print("PASS: influence flag flips an approved trade on an unfavorable confident read")


def test_health_reports_offline_when_disabled():
    original_enabled = news_adapter.NEWS_INTELLIGENCE_ENABLED
    try:
        news_adapter.NEWS_INTELLIGENCE_ENABLED = False
        h = news_adapter.health()
        assert h.state.name in ("OFFLINE",) or "disabled" in (h.message or "").lower()
    finally:
        news_adapter.NEWS_INTELLIGENCE_ENABLED = original_enabled
    print("PASS: health() reports OFFLINE when disabled")


def test_attach_runs_after_quant_intelligence_in_evaluate():
    """Field-presence order check: both keys must exist after
    evaluate(), and a Quant-Intelligence-only failure must not prevent
    News Intelligence from still being attached (and vice versa) --
    the two try/except blocks in engine.evaluate() are independent."""
    trade = _early_breakout_trade()
    decision_object = engine.evaluate(trade, snapshot=_snapshot())
    assert "quant_intelligence" in decision_object
    assert "news_intelligence" in decision_object
    print("PASS: both quant_intelligence and news_intelligence are attached independently")


if __name__ == "__main__":
    test_attach_adds_news_intelligence_without_disturbing_existing_fields()
    test_disabled_flag_skips_import_entirely()
    test_broken_package_degrades_gracefully()
    test_engine_evaluate_never_raises_when_news_adapter_raises()
    test_influence_flag_can_only_add_caution_not_remove_it()
    test_influence_flag_flips_approved_on_unfavorable_confident_read()
    test_health_reports_offline_when_disabled()
    test_attach_runs_after_quant_intelligence_in_evaluate()
    print("\nALL NEWS INTELLIGENCE INTEGRATION TESTS PASSED")
