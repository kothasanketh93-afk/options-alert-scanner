import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import state


def test_state_repo_without_token_fails_loudly(monkeypatch):
    monkeypatch.setenv("STATE_REPO", "me/private-state")
    monkeypatch.delenv("STATE_REPO_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="STATE_REPO_TOKEN"):
        state.ensure_state_branch_checked_out("https://github.com/me/code.git", "t")


def test_state_repo_swaps_url_and_token(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_REPO", "me/private-state")
    monkeypatch.setenv("STATE_REPO_TOKEN", "PRIVTOK")
    monkeypatch.setattr(state, "STATE_CLONE_DIR", tmp_path / "sb")
    calls = []
    monkeypatch.setattr(state, "_run", lambda cmd, cwd=None, check=True: calls.append(cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(state, "_git_identity", lambda cwd: None)
    try:
        state.ensure_state_branch_checked_out("https://github.com/me/code.git", "PUBTOK")
    except Exception:
        pass
    flat = " ".join(" ".join(c) for c in calls)
    assert "me/private-state" in flat and "PRIVTOK" in flat and "PUBTOK" not in flat and "me/code" not in flat
