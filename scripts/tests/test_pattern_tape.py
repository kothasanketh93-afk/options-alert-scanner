import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pattern_tape as pt
import signal_lab as sl
from test_signal_lab import day

WARM = day("2026-10-29", 24000, 0.0)
TODAY = day("2026-10-30", 24100, 3.0)
PREV = (24050, 23950, 24000)


class T(unittest.TestCase):
    def test_tape_matches_full_day_signals_without_lookahead(self):
        rows = sl.compute_rows("NIFTY", WARM, TODAY, PREV)
        first_by_strategy = {}
        for r in rows:
            first_by_strategy.setdefault(r["strategy"], (str(r["signal_time"])[11:16], r["direction"]))
        self.assertTrue(first_by_strategy)
        seen = {}
        for n in range(4, len(TODAY) + 1):
            row = pt.tape_row("NIFTY", WARM, TODAY[:n], PREV)
            for s in filter(None, row["signals_new"].split(";")):
                name, d = s.split(":")
                seen.setdefault(name, (row["bar_time"], d))
        for name, (t, d) in first_by_strategy.items():
            self.assertEqual(seen.get(name), (t, d), name)

    def test_columns_and_patterns(self):
        row = pt.tape_row("NIFTY", WARM, TODAY[:20], PREV)
        self.assertEqual(set(row), set(pt.COLUMNS))
        self.assertEqual(row["symbol"], "NIFTY")
        self.assertIsInstance(row["patterns"], str)

    def test_incomplete_bar_filtered(self):
        now_min = sl._mins(TODAY[10]) + 3          # 11th candle still forming
        done = pt.complete_bars(TODAY[:11], now_min)
        self.assertEqual(len(done), 10)

    def test_too_few_candles(self):
        self.assertIsNone(pt.tape_row("NIFTY", WARM, TODAY[:3], PREV))

    def test_append_header_once(self):
        row = pt.tape_row("NIFTY", WARM, TODAY[:20], PREV)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x" / "t.csv"
            pt.append_rows([row], p); pt.append_rows([row], p)
            lines = p.read_text().strip().splitlines()
            self.assertEqual(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
