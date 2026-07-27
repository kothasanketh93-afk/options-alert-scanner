import unittest

from .helpers import build_isolated_engine, cleanup, make_temp_dir, sample_signal


class TestLearningEngine(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = make_temp_dir()
        self.engine = build_isolated_engine(self.tmp_dir)

    def tearDown(self):
        cleanup(self.tmp_dir)

    def test_record_outcome_updates_weights(self):
        signal = sample_signal()
        result = self.engine.process(signal)
        before = self.engine.weight_manager.get_weights()
        self.engine.record_outcome(result, outcome="WIN", exit_price=70.0)
        after = self.engine.weight_manager.get_weights()
        self.assertNotEqual(before, after)
        self.assertAlmostEqual(sum(after.values()), 1.0, places=6)

    def test_record_outcome_writes_history(self):
        signal = sample_signal()
        result = self.engine.process(signal)
        self.engine.record_outcome(result, outcome="LOSS", exit_price=30.0)
        history = self.engine.learning_engine.recent_history(limit=10)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["outcome"], "LOSS")
        self.assertEqual(history[0]["symbol"], "RELIANCE")

    def test_record_outcome_updates_pattern_db(self):
        signal = sample_signal()
        result = self.engine.process(signal)
        self.assertEqual(result.pattern.sample_size, 0)
        self.engine.record_outcome(result, outcome="WIN", exit_price=70.0)

        result2 = self.engine.process(sample_signal(generated_at=result.processing_ms + 999999))
        self.assertGreaterEqual(result2.pattern.sample_size, 1)

    def test_breakeven_outcome_does_not_move_weights(self):
        signal = sample_signal()
        result = self.engine.process(signal)
        before = self.engine.weight_manager.get_weights()
        self.engine.record_outcome(result, outcome="BREAKEVEN")
        after = self.engine.weight_manager.get_weights()
        self.assertEqual(before, after)

    def test_multiple_outcomes_accumulate_history(self):
        for i in range(5):
            signal = sample_signal(symbol=f"SYM{i}")
            result = self.engine.process(signal)
            self.engine.record_outcome(result, outcome="WIN" if i % 2 == 0 else "LOSS", exit_price=60.0)
        history = self.engine.learning_engine.recent_history(limit=100)
        self.assertEqual(len(history), 5)


if __name__ == "__main__":
    unittest.main()
