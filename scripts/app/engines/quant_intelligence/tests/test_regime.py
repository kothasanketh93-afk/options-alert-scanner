import unittest

from .helpers import build_isolated_engine, cleanup, make_temp_dir, sample_signal


class TestRegimeDetector(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = make_temp_dir()
        self.engine = build_isolated_engine(self.tmp_dir)

    def tearDown(self):
        cleanup(self.tmp_dir)

    def test_strong_trend(self):
        result = self.engine.regime_detector.detect(sample_signal(adx=40))
        self.assertEqual(result.regime, "Strong Trend")

    def test_trending(self):
        result = self.engine.regime_detector.detect(sample_signal(adx=24, volume=30000, avg_volume=30000))
        self.assertEqual(result.regime, "Trending")

    def test_high_volatility(self):
        result = self.engine.regime_detector.detect(sample_signal(atr=40, spot_price=2500, adx=10))
        self.assertEqual(result.regime, "High Volatility")

    def test_low_volatility(self):
        result = self.engine.regime_detector.detect(sample_signal(atr=5, spot_price=2500, adx=10, volume=30000, avg_volume=30000))
        self.assertEqual(result.regime, "Low Volatility")

    def test_expiry_day(self):
        result = self.engine.regime_detector.detect(sample_signal(is_expiry_day=True))
        self.assertEqual(result.regime, "Expiry")

    def test_news_driven(self):
        result = self.engine.regime_detector.detect(sample_signal(is_expiry_day=False, is_news_driven=True))
        self.assertEqual(result.regime, "News Driven")

    def test_gap_up(self):
        result = self.engine.regime_detector.detect(sample_signal(gap_pct=1.2, is_expiry_day=False))
        self.assertEqual(result.regime, "Gap Up")

    def test_gap_down(self):
        result = self.engine.regime_detector.detect(sample_signal(gap_pct=-1.2, is_expiry_day=False))
        self.assertEqual(result.regime, "Gap Down")

    def test_range_or_sideways_default(self):
        result = self.engine.regime_detector.detect(
            sample_signal(adx=8, atr=15, spot_price=2500, volume=25000, avg_volume=25000, gap_pct=0.0)
        )
        self.assertIn(result.regime, ("Range", "Sideways"))

    def test_regime_score_bounded(self):
        for adx in (5, 15, 25, 35, 45):
            result = self.engine.regime_detector.detect(sample_signal(adx=adx))
            self.assertGreaterEqual(result.regime_score, 0)
            self.assertLessEqual(result.regime_score, 100)


if __name__ == "__main__":
    unittest.main()
