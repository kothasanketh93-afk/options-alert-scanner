import unittest
from app.engines.orb_pivot import signals as s


def mk(i, o, h, l, c):
    m = 9 * 60 + 15 + 5 * i
    return {"timestamp": f"2026-10-30T{m // 60:02d}:{m % 60:02d}:00+05:30", "open": o, "high": h, "low": l, "close": c}


BASE = [mk(0, 100, 102, 99, 101), mk(1, 101, 103, 100, 102), mk(2, 102, 103, 100, 101)]  # OR 100..103


class ORB(unittest.TestCase):
    def test_gap_up_break_fresh(self):
        c = BASE + [mk(3, 101, 104, 101, 104)]
        r = s.detect_orb15(c, prev_close=98)
        self.assertEqual(r["direction"], "CE")

    def test_wrong_direction_vs_gap_ignored(self):
        c = BASE + [mk(3, 101, 101, 97, 98)]       # breaks down but gap is up
        self.assertIsNone(s.detect_orb15(c, prev_close=98))

    def test_gap_down_break(self):
        c = BASE + [mk(3, 101, 101, 97, 98)]
        self.assertEqual(s.detect_orb15(c, prev_close=105)["direction"], "PE")

    def test_stale_break_not_chased(self):
        c = BASE + [mk(3, 101, 104, 101, 104), mk(4, 104, 105, 103, 104), mk(5, 104, 105, 103, 104)]
        self.assertIsNone(s.detect_orb15(c, prev_close=98))

    def test_no_break(self):
        self.assertIsNone(s.detect_orb15(BASE + [mk(3, 101, 102, 100, 101)], prev_close=98))

    def test_too_few_candles(self):
        self.assertIsNone(s.detect_orb15(BASE, prev_close=98))


class Pivot(unittest.TestCase):
    # prev H110 L90 C100 -> P=100, R1=110, S1=90
    def cs(self, last):
        return [mk(0, 100, 101, 99, 100), mk(1, 100, 101, 99, 100), mk(2, 100, 101, 99, 109), last]

    def test_r1_break(self):
        r = s.detect_pivot_break(self.cs(mk(3, 109, 112, 109, 111)), 110, 90, 100)
        self.assertEqual((r["direction"], r["level_name"]), ("CE", "R1"))

    def test_red_candle_rejected(self):
        self.assertIsNone(s.detect_pivot_break(self.cs(mk(3, 112, 112, 109, 111)), 110, 90, 100))

    def test_s1_break(self):
        c = [mk(0, 100, 101, 99, 100), mk(1, 100, 101, 99, 100), mk(2, 100, 101, 99, 91), mk(3, 91, 91, 88, 89)]
        self.assertEqual(s.detect_pivot_break(c, 110, 90, 100)["direction"], "PE")

    def test_classic_values(self):
        pv = s.classic_pivots(110, 90, 100)
        self.assertEqual((pv["P"], pv["R1"], pv["S1"]), (100, 110, 90))


class Levels(unittest.TestCase):
    def test_levels(self):
        lv = s.option_levels(100, 0.5, 20, 1.5)    # d=10
        self.assertEqual((lv["stop_loss"], lv["target_1"], lv["target_2"]), (90, 115, 125))

    def test_sl_floor(self):
        self.assertEqual(s.option_levels(10, 0.5, 100, 1.5)["stop_loss"], 5.0)



class Wide(unittest.TestCase):
    def d(self, ranges):
        return [{"high": 100 + r, "low": 100} for r in ranges]

    def test_wide(self):
        self.assertTrue(s.is_wide_prev_day(self.d([10] * 9 + [40])))

    def test_normal(self):
        self.assertFalse(s.is_wide_prev_day(self.d([10] * 9 + [12])))

    def test_insufficient(self):
        self.assertIsNone(s.is_wide_prev_day(self.d([10, 10, 10])))


if __name__ == "__main__":
    unittest.main()


class PDHL(unittest.TestCase):
    # prev high 110, prev low 80. Drift up so EMA9 > EMA21 at the retest.
    UP = [mk(0, 100, 101, 99, 100), mk(1, 100, 102, 100, 101), mk(2, 101, 103, 100, 102),
          mk(3, 102, 106, 101, 105), mk(4, 105, 109, 104, 108)]

    def test_breakout_then_retest_ce(self):
        c = self.UP + [mk(5, 108, 113, 108, 112),            # close 112 > PDH, strong body
                       mk(6, 112, 112, 109, 111),            # drifts, stays above
                       mk(7, 111, 112, 109.5, 111.5)]        # low <= 110? no
        c = c[:6] + [mk(6, 112, 113, 109.5, 111)] + [mk(7, 111, 114, 109.8, 113)]
        r = s.detect_pdhl_retest(c, 110, 80)
        self.assertIsNotNone(r)
        self.assertEqual((r["direction"], r["level_name"]), ("CE", "PDH"))

    def test_wick_only_break_ignored(self):
        c = self.UP + [mk(5, 108, 115, 107, 110.5)]          # body small vs range
        self.assertIsNone(s.detect_pdhl_retest(c, 110, 80))

    def test_failed_breakout_cancels(self):
        c = self.UP + [mk(5, 108, 113, 108, 112), mk(6, 112, 112, 107, 108), mk(7, 108, 112, 108, 111)]
        self.assertIsNone(s.detect_pdhl_retest(c, 110, 80))

    def test_stale_signal_not_chased(self):
        c = self.UP + [mk(5, 108, 113, 108, 112), mk(6, 112, 113, 109.5, 111), mk(7, 111, 114, 110.5, 113),
                       mk(8, 113, 114, 112, 113), mk(9, 113, 115, 112, 114)]
        self.assertIsNone(s.detect_pdhl_retest(c, 110, 80))
