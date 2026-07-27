import concurrent.futures
import time
import unittest

from .helpers import build_isolated_engine, cleanup, make_temp_dir, sample_signal


class TestPerformance(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = make_temp_dir()
        self.engine = build_isolated_engine(self.tmp_dir)

    def tearDown(self):
        cleanup(self.tmp_dir)

    def test_throughput_1000_signals_under_time_budget(self):
        """Spec target: 1000+ signals/day is trivial; this checks 1000
        signals process comfortably within a few seconds locally (well
        under the ~86 seconds/1000 budget implied by 1000/day)."""
        start = time.perf_counter()
        for i in range(1000):
            signal = sample_signal(symbol=f"SYM{i % 50}", strike=2500 + (i % 10))
            result = self.engine.process(signal)
            self.assertIsNotNone(result)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 15.0, f"1000 signals took {elapsed:.2f}s, expected < 15s")

    def test_cache_hit_is_faster_than_cold_path(self):
        signal = sample_signal(symbol="CACHETEST")
        # Cold call
        t0 = time.perf_counter()
        first = self.engine.process(signal)
        cold_ms = (time.perf_counter() - t0) * 1000
        # Cached call (same signal instance -> same cache key)
        t1 = time.perf_counter()
        second = self.engine.process(signal)
        warm_ms = (time.perf_counter() - t1) * 1000
        self.assertEqual(first.probability.probability, second.probability.probability)
        self.assertLessEqual(warm_ms, max(cold_ms, 5.0))

    def test_thread_safety_concurrent_processing(self):
        def worker(i):
            signal = sample_signal(symbol=f"THREAD{i}")
            return self.engine.process(signal)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker, i) for i in range(200)]
            results = [f.result() for f in futures]

        self.assertEqual(len(results), 200)
        self.assertTrue(all(r is not None for r in results))

    def test_concurrent_learning_writes_do_not_corrupt_weights(self):
        signal = sample_signal()
        result = self.engine.process(signal)

        def record(i):
            outcome = "WIN" if i % 2 == 0 else "LOSS"
            self.engine.record_outcome(result, outcome=outcome, exit_price=60.0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(record, range(50)))

        weights = self.engine.weight_manager.get_weights()
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=6)

    def test_cache_respects_max_entries(self):
        small_cache_engine = build_isolated_engine(
            self.tmp_dir, overrides={"cache": {"max_entries": 10, "default_ttl_seconds": 30}}
        )
        for i in range(50):
            small_cache_engine.process(sample_signal(symbol=f"EVICT{i}", strike=1000 + i))
        self.assertLessEqual(len(small_cache_engine.cache), 10)


if __name__ == "__main__":
    unittest.main()
