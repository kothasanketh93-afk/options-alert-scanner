import unittest

from .helpers import build_isolated_engine, cleanup, make_temp_dir, sample_signal


class TestRiskEngine(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = make_temp_dir()
        self.engine = build_isolated_engine(self.tmp_dir)

    def tearDown(self):
        cleanup(self.tmp_dir)

    def test_ce_targets_above_entry(self):
        signal = sample_signal(direction="CE")
        regime = self.engine.regime_detector.detect(signal)
        risk = self.engine.risk_engine.evaluate(signal, regime)
        self.assertLess(risk.stop_loss, risk.entry)
        self.assertGreater(risk.target_1, risk.entry)
        self.assertGreater(risk.target_2, risk.target_1)
        self.assertGreater(risk.target_3, risk.target_2)

    def test_pe_targets_below_entry(self):
        signal = sample_signal(direction="PE")
        regime = self.engine.regime_detector.detect(signal)
        risk = self.engine.risk_engine.evaluate(signal, regime)
        self.assertGreater(risk.stop_loss, risk.entry)
        self.assertLess(risk.target_1, risk.entry)
        self.assertLess(risk.target_2, risk.target_1)
        self.assertLess(risk.target_3, risk.target_2)

    def test_missing_atr_returns_none_levels(self):
        signal = sample_signal(atr=None)
        regime = self.engine.regime_detector.detect(signal)
        risk = self.engine.risk_engine.evaluate(signal, regime)
        self.assertIsNone(risk.stop_loss)
        self.assertEqual(risk.position_score, 0.0)

    def test_risk_reward_ratio_positive(self):
        signal = sample_signal()
        regime = self.engine.regime_detector.detect(signal)
        risk = self.engine.risk_engine.evaluate(signal, regime)
        self.assertIsNotNone(risk.risk_reward_ratio)
        self.assertGreater(risk.risk_reward_ratio, 0)

    def test_high_volatility_widens_stop(self):
        signal_calm = sample_signal(atr=10)
        signal_wild = sample_signal(atr=40)
        regime_calm = self.engine.regime_detector.detect(signal_calm)
        regime_wild = self.engine.regime_detector.detect(signal_wild)
        risk_calm = self.engine.risk_engine.evaluate(signal_calm, regime_calm)
        risk_wild = self.engine.risk_engine.evaluate(signal_wild, regime_wild)
        dist_calm = abs(risk_calm.entry - risk_calm.stop_loss)
        dist_wild = abs(risk_wild.entry - risk_wild.stop_loss)
        self.assertGreater(dist_wild, dist_calm)

    def test_min_stop_distance_floor(self):
        # Tiny ATR relative to price shouldn't produce a near-zero stop distance.
        signal = sample_signal(atr=0.01, spot_price=2500, option_price=45.5)
        regime = self.engine.regime_detector.detect(signal)
        risk = self.engine.risk_engine.evaluate(signal, regime)
        min_distance = risk.entry * (self.engine.config["risk"]["min_stop_distance_pct"] / 100)
        self.assertGreaterEqual(abs(risk.entry - risk.stop_loss), min_distance - 1e-6)

    def test_position_score_bounded(self):
        signal = sample_signal()
        regime = self.engine.regime_detector.detect(signal)
        risk = self.engine.risk_engine.evaluate(signal, regime)
        self.assertGreaterEqual(risk.position_score, 0)
        self.assertLessEqual(risk.position_score, 100)


if __name__ == "__main__":
    unittest.main()
