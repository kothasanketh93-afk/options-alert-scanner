"""Regression: main.py must reach detect_all_orb_pivot_setups (it lives in orb_pivot/engine.py,
not the package). The wiring bug made every ORB/pivot/PDHL shadow step fail from 2026-10-31."""
import main


def test_main_reaches_detect_function():
    assert callable(main.orb_pivot_engine.detect_all_orb_pivot_setups)


def test_create_step_runs_with_stub(monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(main.orb_pivot_engine, "detect_all_orb_pivot_setups", lambda: [])
    assert main.create_orb_pivot_shadow_candidates({"next_seq": {}}, datetime(2026, 11, 3, 10, 0)) is None
