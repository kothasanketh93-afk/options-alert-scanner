import unittest

from ..pattern.matcher import build_pattern_key
from .helpers import build_isolated_engine, cleanup, make_temp_dir, sample_signal


class TestPatternEngine(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = make_temp_dir()
        self.engine = build_isolated_engine(self.tmp_dir)

    def tearDown(self):
        cleanup(self.tmp_dir)

    def test_no_history_returns_zero_sample(self):
        signal = sample_signal()
        regime = self.engine.regime_detector.detect(signal)
        match = self.engine.pattern_matcher.match(signal, regime)
        self.assertEqual(match.sample_size, 0)
        self.assertEqual(match.pattern_confidence, "NONE")

    def test_recorded_outcomes_are_matched_exactly(self):
        signal = sample_signal()
        regime = self.engine.regime_detector.detect(signal)
        key = build_pattern_key(signal, regime)

        for _ in range(6):
            self.engine.pattern_matcher.storage.record_outcome(key, "RELIANCE", "CE", "WIN", target_pct=3.0)
        for _ in range(4):
            self.engine.pattern_matcher.storage.record_outcome(key, "RELIANCE", "CE", "LOSS", sl_pct=1.5)

        match = self.engine.pattern_matcher.match(signal, regime)
        self.assertEqual(match.sample_size, 10)
        self.assertEqual(match.historical_win_pct, 60.0)
        self.assertEqual(match.historical_loss_pct, 40.0)
        self.assertAlmostEqual(match.average_target_pct, 3.0)
        self.assertAlmostEqual(match.average_sl_pct, 1.5)

    def test_pattern_key_is_deterministic(self):
        signal = sample_signal()
        regime = self.engine.regime_detector.detect(signal)
        key1 = build_pattern_key(signal, regime)
        key2 = build_pattern_key(signal, regime)
        self.assertEqual(key1, key2)

    def test_different_direction_changes_key(self):
        ce_signal = sample_signal(direction="CE")
        pe_signal = sample_signal(direction="PE")
        regime = self.engine.regime_detector.detect(ce_signal)
        key_ce = build_pattern_key(ce_signal, regime)
        key_pe = build_pattern_key(pe_signal, regime)
        self.assertNotEqual(key_ce, key_pe)

    def test_confidence_grows_with_sample_size(self):
        signal = sample_signal()
        regime = self.engine.regime_detector.detect(signal)
        key = build_pattern_key(signal, regime)

        for _ in range(2):
            self.engine.pattern_matcher.storage.record_outcome(key, "RELIANCE", "CE", "WIN", target_pct=2.0)
        low_conf = self.engine.pattern_matcher.match(signal, regime).pattern_confidence

        for _ in range(20):
            self.engine.pattern_matcher.storage.record_outcome(key, "RELIANCE", "CE", "WIN", target_pct=2.0)
        high_conf = self.engine.pattern_matcher.match(signal, regime).pattern_confidence

        order = ["NONE", "VERY LOW", "LOW", "MEDIUM", "HIGH", "VERY HIGH"]
        self.assertGreater(order.index(high_conf), order.index(low_conf))


if __name__ == "__main__":
    unittest.main()
