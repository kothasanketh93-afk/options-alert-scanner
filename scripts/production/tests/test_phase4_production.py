"""
scripts/production/tests/test_phase4_production.py -- unit tests for
Part 9 Phase 4: task_manager and cache_manager.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (see test_phase1_production.py
for the same convention). Fully offline / in-process, uses short
sleeps to exercise real threads deterministically.

Run with: python scripts/production/tests/test_phase4_production.py
"""

import os
import sys
import threading
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.task_manager import TaskManager, WorkerState
from production.cache_manager import ManagedCache
from exceptions import WorkerError, CacheError


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


# --------------------------------------------------------------------
# cache_manager
# --------------------------------------------------------------------

def test_basic_get_set_and_expiry():
    cache = ManagedCache(max_entries=10, default_ttl_seconds=0.05)
    cache.set("k", "v")
    assert cache.get("k") == "v"
    time.sleep(0.1)
    assert cache.get("k") is None  # expired


def test_lru_eviction_when_over_capacity():
    cache = ManagedCache(max_entries=3, default_ttl_seconds=None)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    cache.get("a")  # touch a -> now most-recently-used
    cache.set("d", 4)  # should evict "b" (least recently used), not "a"
    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3
    assert cache.get("d") == 4


def test_invalidate_pattern_removes_matching_keys_only():
    cache = ManagedCache(max_entries=100)
    cache.set("option_chain:NIFTY:100", 1)
    cache.set("option_chain:NIFTY:200", 2)
    cache.set("option_chain:BANKNIFTY:100", 3)

    removed = cache.invalidate_pattern("option_chain:NIFTY:*")
    assert removed == 2
    assert cache.get("option_chain:NIFTY:100") is None
    assert cache.get("option_chain:BANKNIFTY:100") == 3


def test_cleanup_expired_sweeps_only_expired_entries():
    cache = ManagedCache(max_entries=100, default_ttl_seconds=0.05)
    cache.set("short", 1)
    cache.set("long", 2, ttl_seconds=10)
    time.sleep(0.1)
    removed = cache.cleanup_expired()
    assert removed == 1
    assert cache.get("long") == 2


def test_get_or_load_computes_once_on_miss():
    cache = ManagedCache()
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return "computed"

    result1 = cache.get_or_load("key", loader)
    result2 = cache.get_or_load("key", loader)
    assert result1 == "computed"
    assert result2 == "computed"
    assert calls["n"] == 1  # second call was a cache hit, loader not called again


def test_get_or_load_single_flight_deduplicates_concurrent_misses():
    cache = ManagedCache()
    calls = {"n": 0}
    call_lock = threading.Lock()

    def slow_loader():
        with call_lock:
            calls["n"] += 1
        time.sleep(0.1)
        return "value"

    results = []
    results_lock = threading.Lock()

    def worker():
        v = cache.get_or_load("shared_key", slow_loader)
        with results_lock:
            results.append(v)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)

    assert calls["n"] == 1, "loader should run exactly once despite 5 concurrent callers"
    assert results == ["value"] * 5


def test_get_or_load_propagates_loader_exception_without_caching():
    cache = ManagedCache()

    def failing_loader():
        raise ValueError("upstream failed")

    try:
        cache.get_or_load("bad_key", failing_loader)
        assert False, "expected ValueError to propagate"
    except ValueError:
        pass
    assert cache.get("bad_key") is None  # nothing cached on failure


def test_get_or_load_validator_rejects_stale_value_and_reloads():
    cache = ManagedCache()
    cache.set("k", "stale_value")
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return "fresh_value"

    result = cache.get_or_load("k", loader, validate=lambda v: v != "stale_value")
    assert result == "fresh_value"
    assert calls["n"] == 1


def test_get_or_load_broken_validator_treated_as_miss_not_crash():
    cache = ManagedCache()
    cache.set("k", "value")

    def broken_validator(_v):
        raise RuntimeError("validator bug")

    def loader():
        return "reloaded"

    result = cache.get_or_load("k", loader, validate=broken_validator)
    assert result == "reloaded"


def test_stats_track_hits_misses_and_evictions():
    cache = ManagedCache(max_entries=1)
    cache.get("missing")
    cache.set("a", 1)
    cache.get("a")
    cache.set("b", 2)  # evicts "a"
    stats = cache.stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 1
    assert stats["evictions"] == 1
    assert stats["entries"] == 1


ALL_TESTS = [
    test_worker_starts_runs_and_stops_cleanly,
    test_starting_an_already_running_worker_is_a_noop_not_a_duplicate,
    test_registering_unknown_name_operations_raise_worker_error,
    test_crashed_worker_auto_restarts_up_to_max_restarts,
    test_check_health_flags_missed_heartbeat,
    test_stop_all_stops_every_registered_worker,
    test_running_count_reflects_active_workers,
    test_basic_get_set_and_expiry,
    test_lru_eviction_when_over_capacity,
    test_invalidate_pattern_removes_matching_keys_only,
    test_cleanup_expired_sweeps_only_expired_entries,
    test_get_or_load_computes_once_on_miss,
    test_get_or_load_single_flight_deduplicates_concurrent_misses,
    test_get_or_load_propagates_loader_exception_without_caching,
    test_get_or_load_validator_rejects_stale_value_and_reloads,
    test_get_or_load_broken_validator_treated_as_miss_not_crash,
    test_stats_track_hits_misses_and_evictions,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
