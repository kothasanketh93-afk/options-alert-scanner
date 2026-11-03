"""maybe_fast_tracker_pass: only with an open live trade, honours the cutoff, sleeps to the 150 s mark, saves once."""
import os, sys
from datetime import datetime, timedelta, timezone

_here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(_here))
import main as m
import config

IST = timezone(timedelta(hours=5, minutes=30))


def _setup(monkey_now, open_trade=True):
    calls = {"track": 0, "save": 0, "sleep": []}
    m.state_mod.ist_now = lambda: monkey_now[0]
    m.run_tracker_step = lambda st, now: calls.__setitem__("track", calls["track"] + 1)
    m.state_mod.save_state = lambda st, commit_message: calls.__setitem__("save", calls["save"] + 1)

    def sleeper(sec):
        calls["sleep"].append(round(sec)); monkey_now[0] += timedelta(seconds=sec)
    st = {"date": "2026-11-03", "trades": ({"a": {"status": "ACTIVE"}} if open_trade else {})}
    return st, calls, sleeper


def test_runs_when_trade_open():
    t0 = datetime(2026, 11, 3, 10, 0, 0, tzinfo=IST); now = [t0 + timedelta(seconds=55)]
    st, calls, sl = _setup(now)
    assert m.maybe_fast_tracker_pass(st, t0, sleep_fn=sl)
    assert calls["track"] == 1 and calls["save"] == 1 and calls["sleep"] == [95]


def test_skips_without_open_trade():
    t0 = datetime(2026, 11, 3, 10, 0, 0, tzinfo=IST); now = [t0 + timedelta(seconds=55)]
    st, calls, sl = _setup(now, open_trade=False)
    assert not m.maybe_fast_tracker_pass(st, t0, sleep_fn=sl) and calls["track"] == 0


def test_cutoff_and_flag():
    t0 = datetime(2026, 11, 3, 15, 24, 0, tzinfo=IST); now = [t0 + timedelta(seconds=55)]
    st, calls, sl = _setup(now)
    assert not m.maybe_fast_tracker_pass(st, t0, sleep_fn=sl)
    t0 = datetime(2026, 11, 3, 10, 0, 0, tzinfo=IST); now = [t0 + timedelta(seconds=55)]
    st, calls, sl = _setup(now)
    config.FAST_TRACKER_ENABLED = False
    try:
        assert not m.maybe_fast_tracker_pass(st, t0, sleep_fn=sl)
    finally:
        config.FAST_TRACKER_ENABLED = True


if __name__ == "__main__":
    test_runs_when_trade_open(); test_skips_without_open_trade(); test_cutoff_and_flag(); print("ok")
