import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import signal_lab as sl


def mk(day, i, o, h, l, c):
    m = 555 + 5 * i
    return {"timestamp": f"{day}T{m//60:02d}:{m%60:02d}:00+05:30", "open": o, "high": h, "low": l, "close": c}


def day(day_s, start, drift, n=75):
    out, p = [], start
    for i in range(n):
        o = p; c = p + drift + (1 if i % 2 else -1)
        out.append(mk(day_s, i, o, max(o, c) + 2, min(o, c) - 2, c)); p = c
    return out


class T(unittest.TestCase):
    def test_gap_up_trend_day_gives_long_rows(self):
        warm = day("2026-10-29", 24000, 0.0)
        today = day("2026-10-30", 24100, 3.0)
        rows = sl.compute_rows("NIFTY", warm, today, (24050, 23950, 24000))
        names = {r["strategy"] for r in rows}
        self.assertIn("orb15_gap", names)
        for r in rows:
            self.assertIn(r["outcome"], ("SL", "TARGET", "EOD"))
            self.assertEqual(r["date"], "2026-10-30")
        self.assertEqual(len([r for r in rows if r["strategy"] == "orb15_gap"]), 2)

    def test_too_few_candles(self):
        self.assertEqual(sl.compute_rows("NIFTY", [], day("2026-10-30", 24000, 1, 5), (1, 1, 1)), [])

    def test_resolve_sl_first(self):
        c = [mk("d", 0, 100, 100, 100, 100), mk("d", 1, 100, 120, 80, 100)]
        self.assertEqual(sl._resolve(c, 1, 1, 10, 1.5)[0], "SL")

    def test_append_idempotent(self):
        rows = sl.compute_rows("NIFTY", day("2026-10-29", 24000, 0), day("2026-10-30", 24100, 3.0), (24050, 23950, 24000))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x" / "sl.csv"
            n = sl.append_rows(rows, p)
            self.assertGreater(n, 0)
            self.assertEqual(sl.append_rows(rows, p), 0)


if __name__ == "__main__":
    unittest.main()
