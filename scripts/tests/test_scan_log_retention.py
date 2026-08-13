"""
tests/test_scan_log_retention.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers the 2026-08-12 centralized troubleshooting log retention feature
(scan_log_store.py). All git operations (_run/_safe_pull/_git_identity) are
stubbed to no-ops -- these tests exercise the file-level retention/append/
export logic against a temporary directory standing in for
scan_log_store.STATE_CLONE_DIR, not real git push/pull (that plumbing is
identical to state.py's own, already covered by this repo's existing
save_state()/load_state() round-trip in production use).

Required cases (verbatim from the spec):
  A. 5 trading-day retention
  B. weekends do not consume retention slots
  C. market holidays do not consume retention slots
  D. oldest trading day is removed when day 6 arrives
  E. current trading day's logs are never prematurely deleted
  F. scanner restart does not duplicate/corrupt logs
  G. multiple 5-minute cycles for the same candidate remain linked
  H. on-demand export contains all selected records
  I. export does not modify stored logs

Run with: python scripts/tests/test_scan_log_retention.py
"""

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

import scan_log_store
import holiday_guard


class _Patch:
    def __init__(self, obj, attr, value):
        self.obj, self.attr, self.value = obj, attr, value
        self._had = hasattr(obj, attr)
        self._orig = getattr(obj, attr, None)

    def __enter__(self):
        setattr(self.obj, self.attr, self.value)
        return self.value

    def __exit__(self, *exc):
        if self._had:
            setattr(self.obj, self.attr, self._orig)
        else:
            delattr(self.obj, self.attr)


def _noop(*args, **kwargs):
    return None


def _noop_run(*args, **kwargs):
    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""
    return _Result()


def _fake_records(n=2, symbol="NIFTY", direction="CE", strike="24600.0", expiry="2026-08-18"):
    return [
        {
            "timestamp_utc": "2026-08-10 04:00:00", "timestamp_ist": "2026-08-10 09:30:00",
            "scan_cycle_id": f"cycle-{i}", "logger": "trading_system.scanner", "level": "INFO",
            "tag": "REJECTED",
            "message": f"REJECTED {symbol} {direction} ATM strike={strike} expiry={expiry} | Reasons: test",
            "underlying": symbol, "direction": direction, "moneyness": "ATM", "strike": strike,
            "expiry": expiry, "candidate_id": f"{symbol}:{direction}:{strike}:{expiry}",
        }
        for i in range(n)
    ]


def _with_temp_store():
    """Context-manager-ish helper: returns a _Patch already entered,
    pointing scan_log_store.STATE_CLONE_DIR at a fresh temp dir, with
    all git operations stubbed to no-ops. Caller must __exit__ it (or
    just let the temp dir get garbage collected -- tests below use
    `with tempfile.TemporaryDirectory()` directly instead for clarity)."""
    pass  # see individual tests -- each opens its own TemporaryDirectory


# ---------------------------------------------------------------------------
# A. 5 trading-day retention
# ---------------------------------------------------------------------------

def test_a_five_trading_day_retention():
    with tempfile.TemporaryDirectory() as tmp:
        with _Patch(scan_log_store, "STATE_CLONE_DIR", Path(tmp)), \
             _Patch(scan_log_store, "_run", _noop_run), \
             _Patch(scan_log_store, "_safe_pull", _noop):
            # 5 consecutive trading-day Mondays..Fridays, Aug 10-14 2026 (real trading days)
            dates = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"]
            for d in dates:
                scan_log_store.flush_to_store(_fake_records(1), date_str=d)
            scan_log_store.cleanup_old_trading_day_logs(today_date_str="2026-08-14", max_trading_days=5)
            remaining = sorted(p.stem for p in (Path(tmp) / scan_log_store.SCAN_LOG_DIR).glob("*.jsonl"))
            assert remaining == dates, f"A: all 5 trading days should survive, got {remaining}"
    print("test_a_five_trading_day_retention passed")


# ---------------------------------------------------------------------------
# B. Weekends do not consume retention slots
# ---------------------------------------------------------------------------

def test_b_weekend_dates_are_never_counted_or_kept():
    """
    Weekend-dated files are never legitimately created by the scanner
    (holiday_guard.should_run() already prevents any run on a weekend),
    so cleanup treats a stray one conservatively -- never guessed at,
    never deleted opportunistically (same "don't invent/delete based on
    assumptions" posture as everywhere else in this pass) -- but,
    critically, it also never COUNTS toward the 5-trading-day budget:
    its presence must not cause a genuine trading day to be evicted
    early. That's the actual, testable meaning of "must not consume a
    retention slot" here.
    """
    with tempfile.TemporaryDirectory() as tmp:
        with _Patch(scan_log_store, "STATE_CLONE_DIR", Path(tmp)), \
             _Patch(scan_log_store, "_run", _noop_run), \
             _Patch(scan_log_store, "_safe_pull", _noop):
            log_dir = Path(tmp) / scan_log_store.SCAN_LOG_DIR
            log_dir.mkdir(parents=True)
            for d in ["2026-08-15", "2026-08-16"]:  # Sat, Sun
                (log_dir / f"{d}.jsonl").write_text(json.dumps(_fake_records(1)[0]) + "\n")
            trading_days = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"]
            for d in trading_days:
                scan_log_store.flush_to_store(_fake_records(1), date_str=d)

            scan_log_store.cleanup_old_trading_day_logs(today_date_str="2026-08-14", max_trading_days=5)
            remaining = sorted(p.stem for p in log_dir.glob("*.jsonl"))
            for d in trading_days:
                assert d in remaining, f"B: weekend files present must not evict a genuine trading day ({d} missing)"
    print("test_b_weekend_dates_are_never_counted_or_kept passed")


# ---------------------------------------------------------------------------
# C. Market holidays do not consume retention slots
# ---------------------------------------------------------------------------

def test_c_market_holidays_are_never_counted_or_kept():
    with tempfile.TemporaryDirectory() as tmp:
        with _Patch(scan_log_store, "STATE_CLONE_DIR", Path(tmp)), \
             _Patch(scan_log_store, "_run", _noop_run), \
             _Patch(scan_log_store, "_safe_pull", _noop):
            log_dir = Path(tmp) / scan_log_store.SCAN_LOG_DIR
            log_dir.mkdir(parents=True)
            # 2026-03-03 is Holi (a genuine NSE weekday holiday, per
            # NSE_MOVABLE_HOLIDAYS_2026) -- a stray file dated then must
            # not be able to displace a real trading day from the budget.
            (log_dir / "2026-03-03.jsonl").write_text(json.dumps(_fake_records(1)[0]) + "\n")
            assert holiday_guard.is_trading_day(
                __import__("datetime").datetime(2026, 3, 3)
            ) is False, "sanity check: Holi 2026-03-03 must not itself read as a trading day"

            trading_days = ["2026-02-27", "2026-03-02", "2026-03-04", "2026-03-05", "2026-03-06"]
            for d in trading_days:
                scan_log_store.flush_to_store(_fake_records(1), date_str=d)

            scan_log_store.cleanup_old_trading_day_logs(today_date_str="2026-03-06", max_trading_days=5)
            remaining = [p.stem for p in log_dir.glob("*.jsonl")]
            for d in trading_days:
                assert d in remaining, f"C: a holiday-dated stray file must not evict a genuine trading day ({d} missing)"
    print("test_c_market_holidays_are_never_counted_or_kept passed")


# ---------------------------------------------------------------------------
# D. Oldest trading day is removed when day 6 arrives
# ---------------------------------------------------------------------------

def test_d_oldest_trading_day_removed_on_sixth_day():
    with tempfile.TemporaryDirectory() as tmp:
        with _Patch(scan_log_store, "STATE_CLONE_DIR", Path(tmp)), \
             _Patch(scan_log_store, "_run", _noop_run), \
             _Patch(scan_log_store, "_safe_pull", _noop):
            week1 = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"]  # Mon-Fri
            for d in week1:
                scan_log_store.flush_to_store(_fake_records(1), date_str=d)
            # Next Monday arrives (2026-08-17) -- 6th trading day
            scan_log_store.flush_to_store(_fake_records(1), date_str="2026-08-17")
            scan_log_store.cleanup_old_trading_day_logs(today_date_str="2026-08-17", max_trading_days=5)

            remaining = sorted(p.stem for p in (Path(tmp) / scan_log_store.SCAN_LOG_DIR).glob("*.jsonl"))
            assert "2026-08-10" not in remaining, "D: the oldest trading day (Mon 08-10) must be removed"
            assert remaining == ["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-17"], (
                f"D: expected Tue-Fri + new Mon, got {remaining}"
            )
    print("test_d_oldest_trading_day_removed_on_sixth_day passed")


# ---------------------------------------------------------------------------
# E. Current trading day's logs are never prematurely deleted
# ---------------------------------------------------------------------------

def test_e_current_trading_day_never_prematurely_deleted():
    with tempfile.TemporaryDirectory() as tmp:
        with _Patch(scan_log_store, "STATE_CLONE_DIR", Path(tmp)), \
             _Patch(scan_log_store, "_run", _noop_run), \
             _Patch(scan_log_store, "_safe_pull", _noop):
            # Even with retention set to 1, today's own file must survive
            # a cleanup call made ON that same day (it's always among the
            # "most recent N", never excluded by definition).
            scan_log_store.flush_to_store(_fake_records(1), date_str="2026-08-14")
            scan_log_store.cleanup_old_trading_day_logs(today_date_str="2026-08-14", max_trading_days=1)
            remaining = [p.stem for p in (Path(tmp) / scan_log_store.SCAN_LOG_DIR).glob("*.jsonl")]
            assert remaining == ["2026-08-14"], "E: today's own file must survive its own day's cleanup"
    print("test_e_current_trading_day_never_prematurely_deleted passed")


# ---------------------------------------------------------------------------
# F. Scanner restart does not duplicate/corrupt logs
# ---------------------------------------------------------------------------

def test_f_restart_appends_without_duplicating_or_corrupting():
    with tempfile.TemporaryDirectory() as tmp:
        with _Patch(scan_log_store, "STATE_CLONE_DIR", Path(tmp)), \
             _Patch(scan_log_store, "_run", _noop_run), \
             _Patch(scan_log_store, "_safe_pull", _noop):
            # "Run 1" writes 2 records, "crashes", "run 2" (a fresh process,
            # simulated by simply calling flush_to_store again) writes 2 more
            # -- never re-reading or rewriting run 1's lines.
            scan_log_store.flush_to_store(_fake_records(2, strike="24600.0"), date_str="2026-08-14")
            scan_log_store.flush_to_store(_fake_records(2, strike="24650.0"), date_str="2026-08-14")

            path = scan_log_store._scan_log_file_path("2026-08-14")
            lines = path.read_text().strip().split("\n")
            assert len(lines) == 4, f"F: expected 4 total lines (2+2, never lost/duplicated), got {len(lines)}"
            for line in lines:
                json.loads(line)  # must not raise -- every line individually valid JSON
    print("test_f_restart_appends_without_duplicating_or_corrupting passed")


# ---------------------------------------------------------------------------
# G. Multiple 5-minute cycles for the same candidate remain linked
# ---------------------------------------------------------------------------

def test_g_same_candidate_linked_across_cycles_via_candidate_id():
    handler1 = scan_log_store.ScanLogCaptureHandler("cycle-0930")
    handler2 = scan_log_store.ScanLogCaptureHandler("cycle-0935")
    record = logging.LogRecord(
        name="trading_system.scanner", level=logging.INFO, pathname="", lineno=0,
        msg="REJECTED NIFTY CE ATM strike=24600.0 | Reasons: Poor Risk Reward",
        args=(), exc_info=None,
    )
    handler1.emit(record)
    handler2.emit(record)  # same underlying evaluation, next 5-min cycle
    id1 = handler1.records[0]["candidate_id"]
    id2 = handler2.records[0]["candidate_id"]
    assert id1 == id2 == "NIFTY:CE:24600.0:?", (
        f"G: the same contract evaluated across two scan cycles must produce the same candidate_id, got {id1!r} / {id2!r}"
    )
    assert handler1.records[0]["scan_cycle_id"] != handler2.records[0]["scan_cycle_id"], (
        "G: scan_cycle_id itself SHOULD differ per cycle -- candidate_id is what links them, not scan_cycle_id"
    )
    print("test_g_same_candidate_linked_across_cycles_via_candidate_id passed")


# ---------------------------------------------------------------------------
# H. On-demand export contains all selected records
# ---------------------------------------------------------------------------

def test_h_export_contains_all_selected_records():
    with tempfile.TemporaryDirectory() as tmp:
        with _Patch(scan_log_store, "STATE_CLONE_DIR", Path(tmp)), \
             _Patch(scan_log_store, "_run", _noop_run), \
             _Patch(scan_log_store, "_safe_pull", _noop):
            scan_log_store.flush_to_store(_fake_records(2, symbol="NIFTY"), date_str="2026-08-13")
            scan_log_store.flush_to_store(_fake_records(3, symbol="BANKNIFTY"), date_str="2026-08-14")

            out = Path(tmp) / "export_all.json"
            count = scan_log_store.export_logs(out)
            assert count == 5, f"H (no filter): expected all 5 records, got {count}"
            exported = json.loads(out.read_text())
            assert len(exported) == 5

            out2 = Path(tmp) / "export_nifty.json"
            count2 = scan_log_store.export_logs(out2, underlying="NIFTY")
            assert count2 == 2, f"H (underlying filter): expected 2 NIFTY records, got {count2}"
    print("test_h_export_contains_all_selected_records passed")


# ---------------------------------------------------------------------------
# I. Export does not modify stored logs
# ---------------------------------------------------------------------------

def test_i_export_does_not_modify_stored_logs():
    with tempfile.TemporaryDirectory() as tmp:
        with _Patch(scan_log_store, "STATE_CLONE_DIR", Path(tmp)), \
             _Patch(scan_log_store, "_run", _noop_run), \
             _Patch(scan_log_store, "_safe_pull", _noop):
            scan_log_store.flush_to_store(_fake_records(2), date_str="2026-08-14")
            path = scan_log_store._scan_log_file_path("2026-08-14")
            before = path.read_text()
            before_mtime = path.stat().st_mtime_ns

            scan_log_store.export_logs(Path(tmp) / "export.json")
            scan_log_store.export_logs(Path(tmp) / "export2.json", only_rejected=True)
            scan_log_store.export_logs(Path(tmp) / "export3.json", candidate_id="NIFTY:CE:24600.0:2026-08-18")

            after = path.read_text()
            after_mtime = path.stat().st_mtime_ns
            assert before == after, "I: exporting must never change the stored file's content"
            assert before_mtime == after_mtime, "I: exporting must never even touch/rewrite the stored file"
    print("test_i_export_does_not_modify_stored_logs passed")


if __name__ == "__main__":
    test_a_five_trading_day_retention()
    test_b_weekend_dates_are_never_counted_or_kept()
    test_c_market_holidays_are_never_counted_or_kept()
    test_d_oldest_trading_day_removed_on_sixth_day()
    test_e_current_trading_day_never_prematurely_deleted()
    test_f_restart_appends_without_duplicating_or_corrupting()
    test_g_same_candidate_linked_across_cycles_via_candidate_id()
    test_h_export_contains_all_selected_records()
    test_i_export_does_not_modify_stored_logs()
    print("\nAll scan-log-retention tests (A-I) passed.")
