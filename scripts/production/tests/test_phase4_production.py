"""
scripts/production/tests/test_phase4_production.py -- unit tests for
Part 9 Phase 4: task_manager.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (see test_phase1_production.py
for the same convention). Fully offline / in-process, uses short
sleeps to exercise real threads deterministically.

(2026-10-30, at the user's explicit request, as part of removing unused
code to make this app intraday-only: cache_manager.py was deleted --
its only real importer besides this test file was production/performance.py,
itself also deleted in the same cleanup; cache.py, the pre-existing
module it was meant to optionally be migrated onto, was never actually
migrated. Its tests below were removed along with it. task_manager.py
is unaffected and kept.)

Run with: python scripts/production/tests/test_phase4_production.py
"""

import os
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.task_manager import TaskManager, WorkerState
from exceptions import WorkerError


# --------------------------------------------------------------------
# task_manager
# --------------------------------------------------------------------

def test_worker_starts_runs_and_stops_cleanly():
    tm = TaskManager()
    iterations = {"n": 0}

    def worker(stop_event, heartbeat):
        while not stop_event.is_set():
            iterations["n"] += 1
            heartbeat()
            stop_event.wait(0.01)

    tm.register("counter", worker, heartbeat_timeout=5.0)
    tm.start("counter")
    time.sleep(0.05)
    assert tm.status("counter").state is WorkerState.RUNNING
    assert iterations["n"] > 0

    stopped = tm.stop("counter", timeout=2.0)
    assert stopped is True
    assert tm.status("counter").state is WorkerState.STOPPED


def test_starting_an_already_running_worker_is_a_noop_not_a_duplicate():
    tm = TaskManager()
    starts = {"n": 0}

    def worker(stop_event, heartbeat):
        starts["n"] += 1
        stop_event.wait(0.2)

    tm.register("once", worker)
    tm.start("once")
    tm.start("once")  # must not spawn a second thread
    time.sleep(0.05)
    assert starts["n"] == 1
    tm.stop("once", timeout=2.0)


def test_registering_unknown_name_operations_raise_worker_error():
    tm = TaskManager()
    try:
        tm.start("ghost")
        assert False, "expected WorkerError"
    except WorkerError:
        pass
    try:
        tm.status("ghost")
        assert False, "expected WorkerError"
    except WorkerError:
        pass


def test_crashed_worker_auto_restarts_up_to_max_restarts():
    tm = TaskManager()
    attempts = {"n": 0}

    def flaky_worker(stop_event, heartbeat):
        attempts["n"] += 1
        raise RuntimeError("boom")

    tm.register("flaky", flaky_worker, restart_on_crash=True, max_restarts=2)
    tm.start("flaky")
    time.sleep(0.2)  # let it crash + restart a couple of times

    assert attempts["n"] == 3  # 1 initial + 2 restarts, then gives up
    status = tm.status("flaky")
    assert status.state is WorkerState.CRASHED
    assert status.restart_count == 2


def test_check_health_flags_missed_heartbeat():
    tm = TaskManager()

    def stuck_worker(stop_event, heartbeat):
        heartbeat()  # one heartbeat, then never again (simulates a deadlock)
        stop_event.wait(1.0)

    tm.register("stuck", stuck_worker, heartbeat_timeout=0.05)
    tm.start("stuck")
    time.sleep(0.15)

    unhealthy = tm.check_health()
    assert any(s.name == "stuck" for s in unhealthy)
    tm.stop("stuck", timeout=2.0)


def test_stop_all_stops_every_registered_worker():
    tm = TaskManager()

    def loop_worker(stop_event, heartbeat):
        while not stop_event.is_set():
            heartbeat()
            stop_event.wait(0.01)

    for name in ("a", "b", "c"):
        tm.register(name, loop_worker)
        tm.start(name)
    time.sleep(0.05)

    results = tm.stop_all(timeout=2.0)
    assert all(results.values())
    assert all(tm.status(n).state is WorkerState.STOPPED for n in ("a", "b", "c"))


def test_running_count_reflects_active_workers():
    tm = TaskManager()

    def loop_worker(stop_event, heartbeat):
        while not stop_event.is_set():
            stop_event.wait(0.01)

    tm.register("x", loop_worker)
    assert tm.running_count == 0
    tm.start("x")
    time.sleep(0.02)
    assert tm.running_count == 1
    tm.stop("x", timeout=2.0)
    assert tm.running_count == 0


ALL_TESTS = [
    test_worker_starts_runs_and_stops_cleanly,
    test_starting_an_already_running_worker_is_a_noop_not_a_duplicate,
    test_registering_unknown_name_operations_raise_worker_error,
    test_crashed_worker_auto_restarts_up_to_max_restarts,
    test_check_health_flags_missed_heartbeat,
    test_stop_all_stops_every_registered_worker,
    test_running_count_reflects_active_workers,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
