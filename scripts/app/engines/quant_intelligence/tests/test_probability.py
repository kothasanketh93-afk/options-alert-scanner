import unittest

from .helpers import build_isolated_engine, cleanup, make_temp_dir, sample_signal


class TestProbabilityEngine(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = make_temp_dir()
        self.engine = build_isolated_engine(self.tmp_dir)

    def tearDown(self):
        cleanup(self.tmp_dir)

    def test_bullish_confluence_scores_high(self):
        signal = sample_signal()
        result = self.engine.probability_engine.score(signal)
        self.assertGreater(result.probability, 55)
        self.assertIn(result.confidence, ("MEDIUM", "HIGH", "VERY HIGH"))

    def test_conflicting_trend_scores_lower(self):
        bullish = self.engine.probability_engine.score(sample_signal())
        bearish_trend_ce = self.engine.probability_engine.score(sample_signal(trend="DOWN"))
        self.assertLess(bearish_trend_ce.probability, bullish.probability)

    def test_missing_fields_never_raise(self):
        minimal = sample_signal(
            ema_fast=None, ema_slow=None, vwap=None, rsi=None, adx=None, atr=None,
            pcr=None, oi_change_pct=None, volume=None, avg_volume=None, iv=None,
            momentum_pct=None, market_breadth=None, breakout_strength=None, candle_structure=None,
        )
        result = self.engine.probability_engine.score(minimal)
        self.assertGreaterEqual(result.probability, 0)
        self.assertLessEqual(result.probability, 100)

    def test_weights_sum_to_one(self):
        weights = self.engine.weight_manager.get_weights()
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=6)

    def test_probability_bounded_0_100(self):
        extreme = sample_signal(rsi=100, adx=90, momentum_pct=5, breakout_strength=1.0)
        result = self.engine.probability_engine.score(extreme)
        self.assertGreaterEqual(result.probability, 0)
        self.assertLessEqual(result.probability, 100)

    def test_reasons_non_empty(self):
        result = self.engine.probability_engine.score(sample_signal())
        self.assertTrue(len(result.reasons) > 0)

    def test_direction_pe_scores_symmetrically(self):
        ce = self.engine.probability_engine.score(sample_signal(direction="CE", trend="UP"))
        pe = self.engine.probability_engine.score(
            sample_signal(
                direction="PE", trend="DOWN", momentum_pct=-0.6, market_breadth=-0.4,
                candle_structure="bearish_engulfing",
            )
        )
        self.assertGreater(pe.probability, 40)
        self.assertGreater(ce.probability, 40)


if __name__ == "__main__":
    unittest.main()
