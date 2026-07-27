"""
production/task_manager.py
----------------------------
Part 9, item 8: Background Task Manager.

Owns every named background worker thread the production system runs
(a periodic resource-monitor sampler, a log-cleanup job, a cache-
cleanup job, the dashboard's own poll loop if it's ever migrated off
asyncio, etc.) so that:

  - starting a worker that's already running is a no-op (or an
    explicit error, your choice) instead of silently creating a
    second thread doing the same work twice
  - every worker is tracked, so nothing becomes an orphan thread that
    outlives the thing that spawned it
  - shutdown is coordinated: stop_all() signals every worker's
    stop_event and joins each with a timeout, instead of the process
    exiting while workers are mid-iteration
  - a worker can be restarted cleanly after a crash (optionally
    automatically, capped by max_restarts so a permanently-broken
    worker doesn't restart-loop forever)
  - each worker reports a heartbeat; check_health() surfaces any
    worker that's stopped heartbeating (stuck, deadlocked) even
    though its thread is technically still alive

Purely additive: no existing script starts a TaskManager unless it
explicitly imports and uses one. Register cleanup with
production.shutdown_manager.shutdown_manager.register("background_tasks",
task_manager.stop_all) to have it torn down on CTRL+C/SIGTERM -- kept
as a manual wiring step rather than an automatic import, so this
module has no hard dependency on shutdown_manager (or vice versa).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from exceptions import WorkerError

try:
    from logging_setup import get_logger
    _logger = get_logger("main")
except Exception:  # pragma: no cover - defensive: never let logging break task management
    _logger = logging.getLogger("task_manager")


class WorkerState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"


@dataclass
class WorkerStatus:
    name: str
    state: WorkerState
    restart_count: int
    last_heartbeat: Optional[float]
    started_at: Optional[float]
    last_error: Optional[str] = None

    def heartbeat_age_seconds(self) -> Optional[float]:
        if self.last_heartbeat is None:
            return None
        return time.time() - self.last_heartbeat


@dataclass
class _Worker:
    name: str
    target: Callable[[threading.Event, Callable[[], None]], None]
    restart_on_crash: bool = True
    max_restarts: int = 5
    heartbeat_timeout: float = 60.0
    stop_timeout: float = 10.0

    thread: Optional[threading.Thread] = field(default=None, repr=False)
    stop_event: threading.Event = field(default_factory=threading.Event)
    state: WorkerState = WorkerState.STOPPED
    restart_count: int = 0
    last_heartbeat: Optional[float] = None
    started_at: Optional[float] = None
    last_error: Optional[str] = None


class TaskManager:
    """One process-wide registry of named background workers. Thread-
    safe: register/start/stop/heartbeat can all be called from any
    thread, including a worker calling its own heartbeat()."""

    def __init__(self):
        self._workers: Dict[str, _Worker] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        target: Callable[[threading.Event, Callable[[], None]], None],
        *,
        restart_on_crash: bool = True,
        max_restarts: int = 5,
        heartbeat_timeout: float = 60.0,
        stop_timeout: float = 10.0,
    ) -> None:
        """target(stop_event, heartbeat) should loop calling
        heartbeat() periodically and checking stop_event.is_set(),
        e.g.:

            def my_worker(stop_event, heartbeat):
                while not stop_event.is_set():
                    do_work()
                    heartbeat()
                    stop_event.wait(30)

        Re-registering a name that already exists replaces its spec
        but does not affect an already-running thread under the old
        spec -- stop it first if you want the new spec to take effect.
        """
        with self._lock:
            if name in self._workers and self._workers[name].state in (
                WorkerState.RUNNING, WorkerState.STARTING,
            ):
                raise WorkerError(f"cannot re-register '{name}' while it is running -- stop it first.")
            self._workers[name] = _Worker(
                name=name, target=target, restart_on_crash=restart_on_crash,
                max_restarts=max_restarts, heartbeat_timeout=heartbeat_timeout,
                stop_timeout=stop_timeout,
            )

    def start(self, name: str) -> None:
        with self._lock:
            worker = self._workers.get(name)
            if worker is None:
                raise WorkerError(f"no worker registered as '{name}'.")
            if worker.state in (WorkerState.RUNNING, WorkerState.STARTING):
                _logger.info("Worker '%s' already running -- start() is a no-op.", name)
                return  # No duplicate workers, per Part 9 item 8.

            worker.stop_event = threading.Event()
            worker.state = WorkerState.STARTING
            worker.last_error = None
            thread = threading.Thread(
                target=self._run_worker, args=(worker,), name=f"worker-{name}", daemon=True,
            )
            worker.thread = thread
            worker.started_at = time.time()
            worker.last_heartbeat = time.time()
            thread.start()
            worker.state = WorkerState.RUNNING
            _logger.info("Worker '%s' started.", name)

    def _run_worker(self, worker: _Worker) -> None:
        def _heartbeat():
            worker.last_heartbeat = time.time()

        try:
            worker.target(worker.stop_event, _heartbeat)
            with self._lock:
                worker.state = WorkerState.STOPPED
            _logger.info("Worker '%s' exited cleanly.", worker.name)
        except Exception as exc:  # noqa: BLE001 - a crashed worker must not crash the process
            with self._lock:
                worker.state = WorkerState.CRASHED
                worker.last_error = str(exc)
            _logger.error("Worker '%s' crashed: %s", worker.name, exc)
            if worker.restart_on_crash and worker.restart_count < worker.max_restarts:
                worker.restart_count += 1
                _logger.warning(
                    "Restarting worker '%s' (attempt %d/%d).",
                    worker.name, worker.restart_count, worker.max_restarts,
                )
                self.start(worker.name)
            elif worker.restart_on_crash:
                _logger.error(
                    "Worker '%s' exceeded max_restarts (%d) -- giving up.",
                    worker.name, worker.max_restarts,
                )

    def stop(self, name: str, timeout: Optional[float] = None) -> bool:
        """Signals the worker to stop and joins its thread. Returns
        True if it stopped within the timeout, False if it's still
        alive (an orphan-avoidance signal, not an orphan itself --
        the thread is daemonized so it can't block process exit, but
        the caller should treat a False return as worth investigating)."""
        with self._lock:
            worker = self._workers.get(name)
            if worker is None or worker.state in (WorkerState.STOPPED,):
                return True
            worker.state = WorkerState.STOPPING
            worker.stop_event.set()
            thread = worker.thread

        if thread is not None:
            thread.join(timeout if timeout is not None else worker.stop_timeout)
            if thread.is_alive():
                _logger.error("Worker '%s' did not stop within timeout.", name)
                return False

        with self._lock:
            worker.state = WorkerState.STOPPED
        _logger.info("Worker '%s' stopped.", name)
        return True

    def stop_all(self, timeout: Optional[float] = None) -> Dict[str, bool]:
        with self._lock:
            names = list(self._workers.keys())
        return {name: self.stop(name, timeout=timeout) for name in names}

    def restart(self, name: str, timeout: Optional[float] = None) -> None:
        self.stop(name, timeout=timeout)
        with self._lock:
            worker = self._workers.get(name)
            if worker is not None:
                worker.restart_count = 0  # manual restart resets the crash-loop counter
        self.start(name)

    def status(self, name: str) -> WorkerStatus:
        with self._lock:
            worker = self._workers.get(name)
            if worker is None:
                raise WorkerError(f"no worker registered as '{name}'.")
            return WorkerStatus(
                name=worker.name, state=worker.state, restart_count=worker.restart_count,
                last_heartbeat=worker.last_heartbeat, started_at=worker.started_at,
                last_error=worker.last_error,
            )

    def all_status(self) -> List[WorkerStatus]:
        with self._lock:
            names = list(self._workers.keys())
        return [self.status(name) for name in names]

    def check_health(self) -> List[WorkerStatus]:
        """Returns workers that are RUNNING but haven't heartbeated
        within their configured timeout -- stuck/deadlocked, distinct
        from CRASHED (which already exited)."""
        unhealthy = []
        for st in self.all_status():
            with self._lock:
                worker = self._workers[st.name]
                timeout = worker.heartbeat_timeout
            if st.state is WorkerState.RUNNING:
                age = st.heartbeat_age_seconds()
                if age is not None and age > timeout:
                    unhealthy.append(st)
        return unhealthy

    @property
    def running_count(self) -> int:
        """Convenient to feed into
        production.resource_monitor.register_gauge("background_task_count", ...)."""
        with self._lock:
            return sum(1 for w in self._workers.values() if w.state is WorkerState.RUNNING)


# Process-wide default instance -- import this rather than constructing
# your own unless you need isolated worker state (e.g. per-test).
task_manager = TaskManager()
