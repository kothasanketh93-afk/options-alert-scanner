import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import heartbeat_check as hc
import state as state_mod

IST = timezone(timedelta(hours=5, minutes=30))


def test_uses_state_last_run_at_not_shallow_git(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "STATE_CLONE_DIR", tmp_path)
    day = state_mod.ist_now().strftime("%Y-%m-%d")
    p = tmp_path / state_mod.STATE_DIR / f"{day}.json"
    p.parent.mkdir(parents=True)
    ts = (state_mod.ist_now() - timedelta(minutes=3)).isoformat(timespec="seconds")
    p.write_text(json.dumps({"last_run_at": ts}))
    got = hc.get_last_trader_commit_time()
    assert abs((state_mod.ist_now() - got).total_seconds() - 180) < 5


def test_falls_back_when_no_state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "STATE_CLONE_DIR", tmp_path)
    monkeypatch.setattr(hc, "_run", lambda *a, **k: type("R", (), {"stdout": ""})())
    assert hc.get_last_trader_commit_time().year == 1970
