import csv, tempfile, unittest
from pathlib import Path
import outcomes_ledger as ol


def tr(tid, status, **kw):
    t = {"trade_id": tid, "status": status, "final_status": status, "symbol": "NIFTY", "direction": "CE",
         "tracked_entry_price": 100.0, "stop_loss": 90.0, "target_2": 125.0, "eod_last_price": 105.0}
    t.update(kw)
    return t


class Ledger(unittest.TestCase):
    def setUp(self):
        self.p = Path(tempfile.mkdtemp()) / "ledger" / "outcomes.csv"

    def rows(self):
        return list(csv.DictReader(open(self.p)))

    def test_result_pct(self):
        self.assertEqual(ol.result_pct(tr("a", "SL_HIT")), -10.0)
        self.assertEqual(ol.result_pct(tr("a", "T2_HIT")), 25.0)
        self.assertEqual(ol.result_pct(tr("a", "EOD_CLOSED")), 5.0)
        self.assertIsNone(ol.result_pct(tr("a", "NOT_ENTERED")))
        self.assertIsNone(ol.result_pct(tr("a", "SL_HIT", tracked_entry_price=None)))

    def test_append_only_terminal_and_dedupe(self):
        st = {"date": "2026-11-02", "shadow_trades": {"a": tr("a", "SL_HIT"), "b": tr("b", "WAITING_ENTRY")},
              "orb_pivot_shadow_trades": {"c": tr("c", "T2_HIT", strategy="orb15", wide_prev_day=False)}}
        self.assertEqual(ol.append_resolved(st, self.p), 2)
        self.assertEqual(ol.append_resolved(st, self.p), 0)      # idempotent
        st["shadow_trades"]["b"]["status"] = st["shadow_trades"]["b"]["final_status"] = "EOD_CLOSED"
        self.assertEqual(ol.append_resolved(st, self.p), 1)
        rows = self.rows()
        self.assertEqual([r["trade_id"] for r in rows], ["a", "c", "b"])
        self.assertEqual(rows[1]["engine"], "orb_pivot")
        self.assertEqual(rows[1]["result_pct"], "25.0")
        self.assertEqual(len([l for l in open(self.p) if l.startswith("trade_id")]), 1)  # one header

    def test_bad_record_skipped(self):
        st = {"date": "d", "shadow_trades": {"x": {"status": "SL_HIT"}, "a": tr("a", "SL_HIT")}}
        self.assertEqual(ol.append_resolved(st, self.p), 1)

    def test_empty_state(self):
        self.assertEqual(ol.append_resolved({"date": "d"}, self.p), 0)
        self.assertFalse(self.p.exists())


if __name__ == "__main__":
    unittest.main()
