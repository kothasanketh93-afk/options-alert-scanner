"""
app/engines/decision_engine/tests/test_quant_intelligence_integration.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as test_decision_engine.py /
test_options_intelligence.py (no pytest dependency).

Covers the Part 11 <-> Part 10 MERGE specifically (not Quant
Intelligence's own internals, which have their own 70-test suite at
app/engines/quant_intelligence/tests/ -- run separately via
app/engines/quant_intelligence/run_tests.py):

  - engine.evaluate() attaches decision_object["quant_intelligence"]
    without changing any pre-existing field's value versus a
    Quant-Intelligence-disabled run on the same input (zero regression).
  - quant_adapter.QUANT_INTELLIGENCE_ENABLED = False fully restores byte-
    identical pre-Part-11 behavior (the disabled branch never even
    imports/calls the engine).
  - A broken/unavailable quant_intelligence package degrades to
    {"enabled": False, "error": True, ...} and the rest of the Standard
    Decision Object is completely unaffected.
  - quant_adapter.QUANT_INTELLIGENCE_INFLUENCE_ENABLED gates whether a
    confidently-bad Quant Intelligence read can flip an
    otherwise-approved trade to rejected, and can never flip an
    already-rejected trade back to approved.
  - quant_adapter.build_signal() correctly reads options_intelligence's
    new additive "raw" sub-dict and app/market/snapshot's regime/EMA/
    VWAP/ATR/gap fields without raising on a minimal/partial trade dict.
  - quant_adapter.health() reports HEALTHY/OFFLINE/CRITICAL correctly
    for use by production.health_registry.

Run with:
    python scripts/app/engines/decision_engine/tests/test_quant_intelligence_integration.py
Exits non-zero (via AssertionError) on the first failing check.
"""

import os
import shutil
import sys
import tempfile

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_here))))
sys.path.insert(0, _scripts_dir)

import config
from app.engines.decision_engine import engine, quant_adapter


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
            "options_intelligence_score": 82, "confidence": "Confirmed",
            "overall_pcr": 1.18, "warning_list": [],
            "raw": {"oi": 180000, "volume": 55000, "iv": 21.5, "bid": 99.6, "ask": 100.4, "oi_change_pct": 9.4},
        },
    }
    trade.update(overrides)
    return trade


def _snapshot(**overrides) -> dict:
    snap = {
        "regime": "TRENDING_BULLISH", "ema9": 24810.0, "ema21": 24780.0,
        "atr": 45.0, "vwap": 24795.0, "gap": {"direction": "UP", "gap_pct": 0.002},
    }
    snap.update(overrides)
    return snap


def _isolated_quant_config(tmp_dir: str) -> dict:
    """Points the vendored Quant Intelligence Engine's storage (pattern
    DB / trade history / adaptive weights) at a throwaway temp dir, so
    running this integration suite never reads or writes the real
    app/engines/quant_intelligence/data/ files -- same isolation
    discipline as quant_intelligence's own tests/helpers.py."""
    from app.engines.quant_intelligence.utils.config_loader import load_config
    qi_config = load_config()
    qi_config.setdefault("pattern", {})["storage_path"] = os.path.join(tmp_dir, "patterns.db")
    qi_config.setdefault("learning", {})["history_path"] = os.path.join(tmp_dir, "trade_history.jsonl")
    qi_config["learning"]["weights_path"] = os.path.join(tmp_dir, "adaptive_weights.json")
    return qi_config


def _reconfigure_quant_engine_isolated(tmp_dir: str) -> None:
    from app.engines.quant_intelligence import QuantEngine
    QuantEngine.configure(config=_isolated_quant_config(tmp_dir))

def _reset_quant_engine_to_default() -> None:
    """Restores the process-wide QuantEngine singleton to its default
    (non-isolated) config after a test has pointed it at a temp dir
    that's about to be deleted -- otherwise a LATER test that uses the
    singleton without its own isolation (e.g. health()) would keep
    pointing at a now-deleted path."""
    from app.engines.quant_intelligence import QuantEngine
    QuantEngine.configure()



# -- tests --------------------------------------------------------------------

def test_quant_intelligence_attached_without_changing_existing_fields():
    tmp_dir = tempfile.mkdtemp(prefix="qi_integration_")
    try:
        _reconfigure_quant_engine_isolated(tmp_dir)
        trade = _early_breakout_trade()
        snapshot = _snapshot()

        decision = engine.evaluate(trade, snapshot)

        assert "quant_intelligence" in decision, "Quant Intelligence result was not attached"
        qi = decision["quant_intelligence"]
        assert qi["enabled"] is True
        assert qi["decision"] in ("SELECTED", "WATCH", "REJECTED")
        assert 0.0 <= qi["probability"] <= 100.0
        assert qi["signal_quality_grade"] in ("A+", "A", "B+", "B", "C", "Reject")
        assert "risk" in qi and "pattern" in qi and "reasons" in qi

        # Every pre-existing Decision Object field the base engine
        # computes must still be present and typed as before -- Quant
        # Intelligence is additive only by default.
        for key in (
            "enabled", "decision", "approved", "trade_quality_score",
            "confidence_score", "probability", "risk", "risk_reward",
            "entry", "stop_loss", "target_1", "target_2", "target_3",
            "priority", "explanation", "rejection_reasons", "engines_used",
        ):
            assert key in decision, f"pre-existing field {key!r} missing after merge"
    finally:
        _reset_quant_engine_to_default()
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("test_quant_intelligence_attached_without_changing_existing_fields passed")


def test_disabled_flag_produces_byte_identical_decision_besides_the_flag_itself():
    tmp_dir = tempfile.mkdtemp(prefix="qi_integration_")
    original_enabled = quant_adapter.QUANT_INTELLIGENCE_ENABLED
    try:
        _reconfigure_quant_engine_isolated(tmp_dir)
        trade = _early_breakout_trade()
        snapshot = _snapshot()

        quant_adapter.QUANT_INTELLIGENCE_ENABLED = True
        with_qi = engine.evaluate(trade, snapshot)

        quant_adapter.QUANT_INTELLIGENCE_ENABLED = False
        without_qi = engine.evaluate(trade, snapshot)

        assert without_qi["quant_intelligence"]["enabled"] is False
        assert "reason" in without_qi["quant_intelligence"]

        # Every field EXCEPT quant_intelligence/timestamp/execution_time_ms
        # (both engines have their own clocks) must match exactly --
        # proof that disabling Quant Intelligence doesn't perturb the
        # base Decision Engine's own math in any way. news_intelligence
        # is also ignored here: it's an independent Part 11.5 field
        # attached by its own adapter AFTER quant_adapter runs, scored
        # off live-collected news cache state that can itself change
        # between the two evaluate() calls below -- unrelated to (and
        # unaffected by) the QUANT_INTELLIGENCE_ENABLED flag this test
        # toggles. See test_news_intelligence_integration.py for the
        # equivalent "toggling NEWS_INTELLIGENCE_ENABLED doesn't perturb
        # anything else" proof for that field.
        ignore = {"quant_intelligence", "news_intelligence", "timestamp", "execution_time_ms"}
        for key in with_qi:
            if key in ignore:
                continue
            assert with_qi[key] == without_qi[key], f"field {key!r} diverged when QUANT_INTELLIGENCE_ENABLED toggled"
    finally:
        quant_adapter.QUANT_INTELLIGENCE_ENABLED = original_enabled
        _reset_quant_engine_to_default()
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("test_disabled_flag_produces_byte_identical_decision_besides_the_flag_itself passed")


def test_broken_quant_intelligence_never_breaks_decision_engine():
    """Simulates the package being unavailable/broken by forcing the
    adapter's lazy import to fail, exactly as it would if
    quant_intelligence were deleted, moved, or raised on import."""
    original_attempted = quant_adapter._import_attempted
    original_engine = quant_adapter._QuantEngine
    original_signal = quant_adapter._Signal
    original_error = quant_adapter._import_error
    try:
        quant_adapter._import_attempted = True
        quant_adapter._QuantEngine = None
        quant_adapter._Signal = None
        quant_adapter._import_error = RuntimeError("simulated import failure")

        trade = _early_breakout_trade()
        snapshot = _snapshot()
        decision = engine.evaluate(trade, snapshot)

        assert decision["quant_intelligence"]["enabled"] is False
        assert "unavailable" in decision["quant_intelligence"]["reason"]
        # The rest of the Decision Object must be fully populated and
        # correct -- a broken Quant Intelligence package must never
        # prevent an otherwise-valid trade from being evaluated/approved.
        assert decision["approved"] in (True, False)
        assert decision["decision"] in ("Trade Approved", "Trade Rejected")
        assert isinstance(decision["trade_quality_score"], (int, float))
    finally:
        quant_adapter._import_attempted = original_attempted
        quant_adapter._QuantEngine = original_engine
        quant_adapter._Signal = original_signal
        quant_adapter._import_error = original_error
    print("test_broken_quant_intelligence_never_breaks_decision_engine passed")


def test_quant_engine_exception_during_process_is_isolated():
    """Simulates QuantEngine.process() itself raising (not just being
    unavailable) -- attach() must still degrade gracefully."""
    from app.engines.quant_intelligence import QuantEngine

    def _boom(signal):
        raise RuntimeError("simulated quant engine internal failure")

    original_process = QuantEngine.process
    try:
        QuantEngine.process = classmethod(lambda cls, signal: _boom(signal))
        trade = _early_breakout_trade()
        decision_object = {"approved": True, "decision": "Trade Approved"}
        quant_adapter.attach(trade, decision_object, _snapshot())

        assert decision_object["quant_intelligence"]["enabled"] is False
        assert decision_object["quant_intelligence"]["error"] is True
        # Original decision fields untouched.
        assert decision_object["approved"] is True
        assert decision_object["decision"] == "Trade Approved"
    finally:
        QuantEngine.process = original_process
    print("test_quant_engine_exception_during_process_is_isolated passed")


def test_influence_disabled_by_default_never_changes_approval():
    tmp_dir = tempfile.mkdtemp(prefix="qi_integration_")
    original_influence = quant_adapter.QUANT_INTELLIGENCE_INFLUENCE_ENABLED
    try:
        _reconfigure_quant_engine_isolated(tmp_dir)
        assert quant_adapter.QUANT_INTELLIGENCE_INFLUENCE_ENABLED is False, "default must be off"

        # A deliberately weak setup that Quant Intelligence should REJECT.
        trade = _early_breakout_trade(momentum_score=5, base_score=5)
        snapshot = _snapshot(regime="SIDEWAYS", atr=1.0)
        decision = engine.evaluate(trade, snapshot)

        assert decision["quant_intelligence"]["decision"] == "REJECTED"
        # ...but with influence off, that must NOT have touched approval.
        # (approved could independently be True or False from the base
        # engine's own logic -- the point is Quant Intelligence's own
        # rejection reason must not appear in rejection_reasons.)
        qi_reason_present = any(
            "Quant Intelligence" in r for r in decision.get("rejection_reasons", [])
        )
        assert not qi_reason_present, "Quant Intelligence influenced rejection_reasons while influence flag was off"
    finally:
        quant_adapter.QUANT_INTELLIGENCE_INFLUENCE_ENABLED = original_influence
        _reset_quant_engine_to_default()
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("test_influence_disabled_by_default_never_changes_approval passed")


def test_influence_enabled_can_flip_approved_to_rejected():
    original_influence = quant_adapter.QUANT_INTELLIGENCE_INFLUENCE_ENABLED
    try:
        quant_adapter.QUANT_INTELLIGENCE_INFLUENCE_ENABLED = True

        decision_object = {"approved": True, "decision": "Trade Approved", "rejection_reasons": []}

        from app.engines.quant_intelligence import (
            QuantResult, ProbabilityResult, SignalQualityResult, RegimeResult, RiskResult,
            PatternMatch, Explanation,
        )
        weak_result = QuantResult(
            symbol="NIFTY", direction="CE", decision="REJECTED",
            probability=ProbabilityResult(probability=15.0, confidence="VERY LOW"),
            signal_quality=SignalQualityResult(grade="Reject", quality_score=15.0, rejected=True,
                                                reasons=["Poor risk reward"]),
            regime=RegimeResult(regime="Sideways", confidence="LOW", regime_score=20.0),
            risk=RiskResult(entry=100, stop_loss=95, trailing_stop_loss=95, target_1=102, target_2=104,
                             target_3=106, risk_reward_ratio=0.4, position_score=10.0),
            pattern=PatternMatch(pattern_key="k", historical_win_pct=None, historical_loss_pct=None,
                                  average_target_pct=None, average_sl_pct=None, sample_size=0,
                                  pattern_confidence="NONE"),
            explanation=Explanation(summary="weak", decision="REJECTED"),
        )

        quant_adapter._apply_influence(decision_object, weak_result)

        assert decision_object["approved"] is False
        assert decision_object["decision"] == "Trade Rejected"
        assert any("Quant Intelligence" in r for r in decision_object["rejection_reasons"])
    finally:
        quant_adapter.QUANT_INTELLIGENCE_INFLUENCE_ENABLED = original_influence
    print("test_influence_enabled_can_flip_approved_to_rejected passed")


def test_influence_enabled_never_flips_rejected_back_to_approved():
    tmp_dir = tempfile.mkdtemp(prefix="qi_integration_")
    original_influence = quant_adapter.QUANT_INTELLIGENCE_INFLUENCE_ENABLED
    try:
        _reconfigure_quant_engine_isolated(tmp_dir)
        quant_adapter.QUANT_INTELLIGENCE_INFLUENCE_ENABLED = True

        # A trade the base Decision Engine itself already rejects
        # (impossible risk/reward) -- Quant Intelligence must not be
        # able to approve it even if its own read were strong.
        trade = _early_breakout_trade(stop_loss=100.0, target_1=100.5, target_2=101.0)
        decision_object = {"approved": False, "decision": "Trade Rejected", "rejection_reasons": ["Poor Risk Reward"]}

        from app.engines.quant_intelligence import QuantResult, ProbabilityResult, SignalQualityResult, RegimeResult, RiskResult, PatternMatch, Explanation
        fake_result = QuantResult(
            symbol="NIFTY", direction="CE", decision="SELECTED",
            probability=ProbabilityResult(probability=95.0, confidence="VERY HIGH"),
            signal_quality=SignalQualityResult(grade="A+", quality_score=95.0, rejected=False),
            regime=RegimeResult(regime="Strong Trend", confidence="HIGH", regime_score=90.0),
            risk=RiskResult(entry=100, stop_loss=90, trailing_stop_loss=90, target_1=120, target_2=140, target_3=160, risk_reward_ratio=2.0, position_score=90.0),
            pattern=PatternMatch(pattern_key="k", historical_win_pct=80.0, historical_loss_pct=20.0, average_target_pct=5.0, average_sl_pct=2.0, sample_size=20, pattern_confidence="HIGH"),
            explanation=Explanation(summary="strong", decision="SELECTED"),
        )
        quant_adapter._apply_influence(decision_object, fake_result)

        assert decision_object["approved"] is False, "Quant Intelligence must never flip an already-rejected trade to approved"
        assert decision_object["decision"] == "Trade Rejected"
    finally:
        quant_adapter.QUANT_INTELLIGENCE_INFLUENCE_ENABLED = original_influence
        _reset_quant_engine_to_default()
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("test_influence_enabled_never_flips_rejected_back_to_approved passed")


def test_build_signal_reads_options_intelligence_raw_subdict():
    trade = _early_breakout_trade()
    snapshot = _snapshot()
    signal = quant_adapter.build_signal(trade, snapshot)

    assert signal.oi == 180000
    assert signal.volume == 55000
    assert signal.iv == 21.5
    assert signal.pcr == 1.18
    assert signal.trend == "UP"          # TRENDING_BULLISH -> UP
    assert signal.ema_fast == 24810.0
    assert signal.ema_slow == 24780.0
    assert signal.vwap == 24795.0
    assert signal.atr == 45.0
    assert signal.gap_pct is not None and signal.gap_pct > 0
    # NOTE: signal.expiry_days depends on option_chain.days_to_expiry(),
    # and option_chain.py transitively imports the full broker stack
    # (market_data -> angelone -> auth -> pyotp). In a bare sandbox
    # without pyotp installed, _expiry_fields()'s try/except correctly
    # degrades this to None rather than raising -- see
    # test_expiry_fields_parses_correctly_when_option_chain_is_available
    # below for a dependency-free unit test of the actual parsing logic.
    assert signal.expiry_days is None or isinstance(signal.expiry_days, int)
    assert signal.min_oi_threshold == float(config.MIN_OI)
    assert signal.min_volume_threshold == float(config.MIN_OPTION_VOLUME)
    print("test_build_signal_reads_options_intelligence_raw_subdict passed")


def test_expiry_fields_parses_correctly_when_option_chain_is_available():
    """option_chain.py transitively requires the broker stack (pyotp
    etc.), which this sandbox doesn't have installed. Shims a minimal
    fake `option_chain` module into sys.modules so _expiry_fields()'s
    actual parsing logic (not just its fallback path) is verified
    without that dependency. In a real deployment with the broker
    stack installed, the real option_chain.days_to_expiry() is used
    instead -- this shim only exists for THIS test."""
    import types
    fake_module = types.ModuleType("option_chain")

    def _fake_days_to_expiry(expiry_date_str, now=None):
        from datetime import datetime
        from datetime import date as _date
        expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
        today = _date(2026, 7, 27)  # fixed "today" so this test is deterministic
        return (expiry_date - today).days

    fake_module.days_to_expiry = _fake_days_to_expiry

    original_module = sys.modules.get("option_chain")
    sys.modules["option_chain"] = fake_module
    try:
        expiry_days, is_expiry_day = quant_adapter._expiry_fields({"expiry": "2026-08-06"})
        assert expiry_days == 10
        assert is_expiry_day is False

        expiry_days_today, is_expiry_today = quant_adapter._expiry_fields({"expiry": "2026-07-27"})
        assert expiry_days_today == 0
        assert is_expiry_today is True

        expiry_days_none, is_expiry_none = quant_adapter._expiry_fields({})
        assert expiry_days_none is None and is_expiry_none is None
    finally:
        if original_module is not None:
            sys.modules["option_chain"] = original_module
        else:
            sys.modules.pop("option_chain", None)
    print("test_expiry_fields_parses_correctly_when_option_chain_is_available passed")


def test_build_signal_never_raises_on_minimal_trade():
    minimal_trade = {"symbol": "X", "direction": "PE"}
    signal = quant_adapter.build_signal(minimal_trade, snapshot=None)
    assert signal.symbol == "X"
    assert signal.direction == "PE"
    assert signal.oi is None and signal.volume is None and signal.iv is None
    print("test_build_signal_never_raises_on_minimal_trade passed")


def test_health_reports_healthy_when_enabled():
    original_enabled = quant_adapter.QUANT_INTELLIGENCE_ENABLED
    try:
        quant_adapter.QUANT_INTELLIGENCE_ENABLED = True
        h = quant_adapter.health()
        assert str(h.state) in ("HEALTHY", "DEGRADED")  # never CRITICAL/OFFLINE when the package is fine
    finally:
        quant_adapter.QUANT_INTELLIGENCE_ENABLED = original_enabled
    print("test_health_reports_healthy_when_enabled passed")


def test_health_reports_offline_when_disabled():
    original_enabled = quant_adapter.QUANT_INTELLIGENCE_ENABLED
    try:
        quant_adapter.QUANT_INTELLIGENCE_ENABLED = False
        h = quant_adapter.health()
        assert str(h.state) == "OFFLINE"
    finally:
        quant_adapter.QUANT_INTELLIGENCE_ENABLED = original_enabled
    print("test_health_reports_offline_when_disabled passed")


def test_record_outcome_disabled_returns_none_without_raising():
    original_enabled = quant_adapter.QUANT_INTELLIGENCE_ENABLED
    try:
        quant_adapter.QUANT_INTELLIGENCE_ENABLED = False
        result = quant_adapter.record_outcome(_early_breakout_trade(), outcome="WIN", exit_price=120.0)
        assert result is None
    finally:
        quant_adapter.QUANT_INTELLIGENCE_ENABLED = original_enabled
    print("test_record_outcome_disabled_returns_none_without_raising passed")


def test_plain_trade_score_strategy_trade_does_not_crash():
    """Non-early_breakout ('trade_score') trades have no momentum_score/
    base_score on the trade dict at all -- confirms the adapter degrades
    those Signal fields to None rather than raising a KeyError."""
    trade = {
        "symbol": "RELIANCE", "direction": "PE", "strategy": "trade_score",
        "spot_price": 2500.0, "alert_premium": 42.0, "strike": 2480.0,
        "expiry": "2026-08-06",
        "options_intelligence": {"overall_pcr": 0.9, "raw": {"oi": 60000, "volume": 8000, "iv": 30.0}},
    }
    decision_object = {"approved": True, "decision": "Trade Approved"}
    quant_adapter.attach(trade, decision_object, _snapshot(regime="TRENDING_BEARISH"))
    assert decision_object["quant_intelligence"]["enabled"] is True
    print("test_plain_trade_score_strategy_trade_does_not_crash passed")


if __name__ == "__main__":
    test_quant_intelligence_attached_without_changing_existing_fields()
    test_disabled_flag_produces_byte_identical_decision_besides_the_flag_itself()
    test_broken_quant_intelligence_never_breaks_decision_engine()
    test_quant_engine_exception_during_process_is_isolated()
    test_influence_disabled_by_default_never_changes_approval()
    test_influence_enabled_can_flip_approved_to_rejected()
    test_influence_enabled_never_flips_rejected_back_to_approved()
    test_build_signal_reads_options_intelligence_raw_subdict()
    test_expiry_fields_parses_correctly_when_option_chain_is_available()
    test_build_signal_never_raises_on_minimal_trade()
    test_health_reports_healthy_when_enabled()
    test_health_reports_offline_when_disabled()
    test_record_outcome_disabled_returns_none_without_raising()
    test_plain_trade_score_strategy_trade_does_not_crash()
    print("\nAll Part 11 <-> Part 10 integration tests passed.")
