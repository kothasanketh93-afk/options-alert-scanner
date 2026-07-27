import unittest

from ..models.signal import Signal
from ..utils.exceptions import ValidationError
from .helpers import build_isolated_engine, cleanup, make_temp_dir, sample_signal


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = make_temp_dir()
        self.engine = build_isolated_engine(self.tmp_dir)

    def tearDown(self):
        cleanup(self.tmp_dir)

    def test_rejects_non_signal_input(self):
        with self.assertRaises(ValidationError):
            self.engine.process({"symbol": "X"})  # type: ignore[arg-type]

    def test_rejects_missing_symbol(self):
        with self.assertRaises(ValidationError):
            self.engine.process(Signal(symbol="", direction="CE"))

    def test_rejects_invalid_direction(self):
        with self.assertRaises(ValidationError):
            self.engine.process(Signal(symbol="X", direction="LONG"))

    def test_all_optional_fields_none(self):
        bare = Signal(symbol="BARE", direction="CE")
        result = self.engine.process(bare)
        self.assertEqual(result.symbol, "BARE")
        self.assertEqual(result.signal_quality.grade, "Reject")
        self.assertIsNone(result.risk.stop_loss)

    def test_zero_atr_does_not_crash(self):
        signal = sample_signal(atr=0.0)
        result = self.engine.process(signal)
        self.assertIsNone(result.risk.stop_loss)

    def test_negative_prices_do_not_crash(self):
        # Malformed upstream data -- engine should degrade gracefully, not raise.
        signal = sample_signal(spot_price=-100, option_price=-5, atr=2)
        result = self.engine.process(signal)
        self.assertIsNotNone(result)

    def test_extreme_indicator_values_are_clamped(self):
        signal = sample_signal(rsi=1e9, adx=1e9, momentum_pct=1e9, breakout_strength=1e9)
        result = self.engine.process(signal)
        self.assertGreaterEqual(result.probability.probability, 0)
        self.assertLessEqual(result.probability.probability, 100)

    def test_unknown_candle_structure_is_neutral(self):
        signal = sample_signal(candle_structure="totally_unknown_pattern_xyz")
        result = self.engine.process(signal)
        self.assertIsNotNone(result)

    def test_disabled_engine_returns_neutral_result_not_crash(self):
        engine = build_isolated_engine(
            self.tmp_dir, overrides={"engines": {"risk": {"enabled": False}}}
        )
        result = engine.process(sample_signal())
        self.assertIsNone(result.risk.stop_loss)
        self.assertIn("Risk engine disabled", result.risk.reasons[0])

    def test_all_engines_disabled_still_returns_result(self):
        engine = build_isolated_engine(
            self.tmp_dir,
            overrides={
                "engines": {
                    "probability": {"enabled": False},
                    "signal_quality": {"enabled": False},
                    "regime": {"enabled": False},
                    "risk": {"enabled": False},
                    "pattern": {"enabled": False},
                    "explainability": {"enabled": False},
                    "cache": {"enabled": False},
                }
            },
        )
        result = engine.process(sample_signal())
        self.assertIsNotNone(result)
        self.assertEqual(result.decision, "WATCH")

    def test_extra_unknown_fields_preserved_not_rejected(self):
        signal = Signal.from_dict(
            {
                "symbol": "EXTRA",
                "direction": "CE",
                "some_future_field": 42,
            }
        )
        self.assertEqual(signal.extra.get("some_future_field"), 42)
        result = self.engine.process(signal)
        self.assertIsNotNone(result)

    def test_extremely_tight_expiry(self):
        signal = sample_signal(expiry_days=0, is_expiry_day=True)
        result = self.engine.process(signal)
        self.assertEqual(result.regime.regime, "Expiry")

    def test_symbol_with_special_characters_in_pattern_key(self):
        signal = sample_signal(symbol="NIFTY-24JUL-25000-CE")
        result = self.engine.process(signal)
        self.assertIn("|", result.pattern.pattern_key)

    def test_record_outcome_with_missing_optional_fields(self):
        result = self.engine.process(sample_signal())
        outcome = self.engine.record_outcome(result, outcome="win")  # lowercase on purpose
        self.assertIn("weights", outcome)

    def test_repeated_process_calls_are_idempotent_within_cache_ttl(self):
        signal = sample_signal(symbol="IDEMPOTENT")
        first = self.engine.process(signal)
        second = self.engine.process(signal)
        self.assertEqual(first.probability.probability, second.probability.probability)
        self.assertEqual(first.pattern.pattern_key, second.pattern.pattern_key)


if __name__ == "__main__":
    unittest.main()
