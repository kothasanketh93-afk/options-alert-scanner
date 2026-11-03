import os, sys
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import run_gap, outcomes_ledger


def test_gap_alert_once_and_not_outside_market():
    sent = []
    st = {"last_run_at": "2026-11-04T10:00:00"}
    assert run_gap.check(st, datetime(2026, 11, 4, 10, 30), sent.append) is True
    assert len(sent) == 1 and st["last_run_at"].startswith("2026-11-04T10:30") and st["run_gaps"] == 1
    assert run_gap.check(st, datetime(2026, 11, 4, 10, 35), sent.append) is False   # normal 5-min cadence
    st2 = {"last_run_at": "2026-11-04T08:00:00"}
    assert run_gap.check(st2, datetime(2026, 11, 4, 10, 30), sent.append) is False  # prev run before market
    assert run_gap.check({}, datetime(2026, 11, 4, 10, 30), sent.append) is False   # first run of day


def test_entry_bucket():
    f = outcomes_ledger.entry_bucket
    assert f("2026-11-03T09:31:00") == "0915-0945" and f("2026-11-03T10:20:00") == "0945-1130"
    assert f("2026-11-03T14:25:00") == "1400+" and f(None) == ""
