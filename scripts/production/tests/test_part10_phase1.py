"""
scripts/production/tests/test_part10_phase1.py -- unit tests for
Part 10 Phase 1: health_states.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (matches test_phase1_production.py
and friends). Fully offline / in-process.

(2026-10-30, at the user's explicit request, as part of removing unused
code to make this app intraday-only: system_monitor.py was deleted --
zero importers anywhere in the repo outside this file's own tests;
production/health_states.py only ever mentioned it in a comment, never
an import. Its 4 tests below were removed along with it.)

(2026-10-31, at the user's explicit request, as part of removing the
unused dashboard stack: health_registry.py was deleted -- its only
importers were app/backend and other deleted production/ modules. Its 8
tests below were removed with it. health_states.py is live (imported by
decision_engine's quant_adapter/news_adapter) and kept.)

Run with: python scripts/production/tests/test_part10_phase1.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.health_states import (
    HealthState, ComponentHealth, combine, combine_components, state_from_pct,
)


# --------------------------------------------------------------------
# health_states
# --------------------------------------------------------------------

def test_health_state_ordering_worst_wins():
    assert combine([HealthState.HEALTHY, HealthState.WARNING]) == HealthState.WARNING
    assert combine([HealthState.CRITICAL, HealthState.HEALTHY, HealthState.OFFLINE]) == HealthState.OFFLINE
    assert combine([]) == HealthState.HEALTHY


def test_str_of_health_state_is_the_name():
    assert str(HealthState.DEGRADED) == "DEGRADED"


def test_combine_components_uses_component_states():
    components = [
        ComponentHealth(name="a", state=HealthState.HEALTHY, message="ok"),
        ComponentHealth(name="b", state=HealthState.CRITICAL, message="bad"),
    ]
    assert combine_components(components) == HealthState.CRITICAL


def test_component_health_to_dict_shape():
    c = ComponentHealth(name="scanner", state=HealthState.WARNING, message="slow", metrics={"x": 1})
    d = c.to_dict()
    assert d["name"] == "scanner"
    assert d["state"] == "WARNING"
    assert d["metrics"] == {"x": 1}
    assert "checked_at" in d


def test_state_from_pct_thresholds():
    assert state_from_pct(10.0) == HealthState.HEALTHY
    assert state_from_pct(80.0) == HealthState.WARNING
    assert state_from_pct(92.0) == HealthState.DEGRADED
    assert state_from_pct(99.0) == HealthState.CRITICAL


def test_state_from_pct_none_is_warning_not_healthy():
    assert state_from_pct(None) == HealthState.WARNING


ALL_TESTS = [
    test_health_state_ordering_worst_wins,
    test_str_of_health_state_is_the_name,
    test_combine_components_uses_component_states,
    test_component_health_to_dict_shape,
    test_state_from_pct_thresholds,
    test_state_from_pct_none_is_warning_not_healthy,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
