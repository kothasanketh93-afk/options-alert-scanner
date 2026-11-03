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

    def test_net_and_expiry_day(self):
        import config
        config.LEDGER_ROUND_TRIP_COST_PCT = 1.5
        st = {"date": "2026-11-03", "shadow_trades": {"a": tr("a", "T2_HIT", expiry="2026-11-03", entry_oi=900)}}
        ol.append_resolved(st, self.p)
        r = self.rows()[0]
        self.assertEqual((r["result_pct"], r["net_result_pct"], r["expiry_day"], r["entry_oi"]),
                         ("25.0", "23.5", "True", "900"))

    def test_bad_record_skipped(self):
        st = {"date": "d", "shadow_trades": {"x": {"status": "SL_HIT"}, "a": tr("a", "SL_HIT")}}
        self.assertEqual(ol.append_resolved(st, self.p), 1)

    def test_t1_then_sl_and_trailing_are_recorded_with_loss(self):
        t1 = tr("t1", "SL_HIT", final_status="T1_THEN_SL")
        tt = tr("t2", "SL_HIT", final_status="T1_THEN_TRAILING_SL", stop_loss=100.5)
        eod = tr("t3", "EOD_CLOSED", final_status="T1_HIT_THEN_EOD")
        live = tr("t4", "SL_HIT", final_status="T1_THEN_SL")
        state = {"date": "2026-11-03", "trades": {"x": live}, "shadow_trades": {"a": t1, "b": tt, "c": eod}}
        self.assertEqual(ol.append_resolved(state, self.p), 4)
        by = {r["trade_id"]: r for r in self.rows()}
        self.assertEqual(by["t1"]["result_pct"], "-10.0")      # T1 touched, then the original SL: a loss
        self.assertEqual(by["t2"]["result_pct"], "0.5")        # trailing lock
        self.assertEqual(by["t3"]["result_pct"], "5.0")
        self.assertEqual(by["t4"]["engine"], "live")

    def test_active_trade_not_recorded(self):
        self.assertEqual(ol.append_resolved({"date": "d", "shadow_trades": {"a": tr("a", "T1_HIT", final_status=None)}}, self.p), 0)

    def test_empty_state(self):
        self.assertEqual(ol.append_resolved({"date": "d"}, self.p), 0)
        self.assertFalse(self.p.exists())


if __name__ == "__main__":
    unittest.main()


class Variants(unittest.TestCase):
    def test_t1_variants(self):
        base = dict(t1_hit=True, target_1=110.0)
        # stopped out after T1: breakeven rule exits at entry (0), half-and-half = +5%
        t = tr("a", "SL_HIT", final_status="T1_THEN_SL", **base)
        self.assertEqual(ol.t1_variants(t, ol.result_pct(t)), (0.0, 5.0))
        # T2 reached without returning to entry
        t = tr("b", "T2_HIT", be_tracked=True, **base)
        self.assertEqual(ol.t1_variants(t, ol.result_pct(t)), (25.0, 17.5))
        # T2 reached but price had returned to entry first: breakeven rule would have exited flat
        t = tr("c", "T2_HIT", be_tracked=True, be_touch_price=100.0, **base)
        self.assertEqual(ol.t1_variants(t, ol.result_pct(t)), (0.0, 5.0))
        # never hit T1: unchanged; unknown path on old record: None
        t = tr("d", "SL_HIT")
        self.assertEqual(ol.t1_variants(t, ol.result_pct(t)), (-10.0, -10.0))
        t = tr("e", "T2_HIT", **base)
        self.assertEqual(ol.t1_variants(t, ol.result_pct(t)), (None, None))

    def test_header_migration(self):
        p = Path(tempfile.mkdtemp()) / "o.csv"
        old = [c for c in ol.COLUMNS if c not in ("be_after_t1_pct", "half_t1_plus_be_pct")]
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=old); w.writeheader(); w.writerow({c: "x" for c in old})
        n = ol.append_resolved({"date": "d", "shadow_trades": {"a": tr("a", "SL_HIT")}}, p)
        rows = list(csv.DictReader(open(p)))
        self.assertEqual((n, len(rows), list(rows[0].keys())), (1, 2, ol.COLUMNS))
