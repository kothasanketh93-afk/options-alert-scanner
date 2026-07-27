import unittest

from .helpers import build_isolated_engine, cleanup, make_temp_dir, sample_signal


class TestSignalQualityEngine(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = make_temp_dir()
        self.engine = build_isolated_engine(self.tmp_dir)

    def tearDown(self):
        cleanup(self.tmp_dir)

    def _run(self, signal):
        probability = self.engine.probability_engine.score(signal)
        regime = self.engine.regime_detector.detect(signal)
        risk = self.engine.risk_engine.evaluate(signal, regime)
        quality = self.engine.signal_quality_engine.evaluate(signal, probability, risk.risk_reward_ratio)
        return probability, regime, risk, quality

    def test_strong_setup_gets_real_grade(self):
        _, _, _, quality = self._run(sample_signal())
        self.assertIn(quality.grade, ("A+", "A", "B+", "B", "C"))
        self.assertFalse(quality.rejected)

    def test_late_entry_forces_reject(self):
        import time
        stale = sample_signal(generated_at=time.time() - 60 * 60)
        _, _, _, quality = self._run(stale)
        self.assertEqual(quality.grade, "Reject")
        self.assertTrue(any("Late entry" in r for r in quality.reasons))

    def test_weak_volume_forces_reject(self):
        thin = sample_signal(volume=100, min_volume_threshold=20000)
        _, _, _, quality = self._run(thin)
        self.assertEqual(quality.grade, "Reject")
        self.assertTrue(any("Weak volume" in r for r in quality.reasons))

    def test_poor_liquidity_forces_reject(self):
        illiquid = sample_signal(oi=100, min_oi_threshold=50000, spread_pct=5.0)
        _, _, _, quality = self._run(illiquid)
        self.assertEqual(quality.grade, "Reject")

    def test_weak_momentum_forces_reject(self):
        flat = sample_signal(momentum_pct=0.0)
        _, _, _, quality = self._run(flat)
        self.assertEqual(quality.grade, "Reject")

    def test_quality_score_within_bounds(self):
        _, _, _, quality = self._run(sample_signal())
        self.assertGreaterEqual(quality.quality_score, 0)
        self.assertLessEqual(quality.quality_score, 100)


if __name__ == "__main__":
    unittest.main()
