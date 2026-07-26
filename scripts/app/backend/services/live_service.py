"""
live_service.py
-----------------
Phase 3 -- poll-and-diff WebSocket streaming.

Design decision made explicitly before writing this (see README.md's
"Phase 3" section for the full rationale): poll-and-diff, not
publish-on-write. This module never touches main.py or any engine --
it calls the exact same Phase 1/2 services (scanner_service,
health_service, replay_service) that the REST routes already call, on
a timer, and returns only the pieces that changed since the last
cycle. No new data source, no new business logic -- purely a
transport layer over what already exists.

Honest scope, matching the Part 8 spec's Phase 3 event list:
  - "Live Scanner Status"            -> scanner_service.get_status()
  - "New Signals"                    -> trade_ids appearing in today's
                                         state that weren't there last
                                         cycle
  - "Decision Updates" / "Trade
    Updates"                         -> status changes on trade_ids
                                         that already existed. There is
                                         no separate "decision" feed
                                         distinct from state["trades"]
                                         in the existing app -- the
                                         same honest limitation
                                         scanner_service.get_latest_
                                         signals() already documents
                                         for the REST API applies here,
                                         so both spec items map to the
                                         one real event type below.
  - "System Health"                  -> health_service.get_health()
  - "Replay Progress"                -> journal entry count for today
                                         + daily/weekly/monthly summary
                                         availability flipping to True.
                                         A real proxy for the offline
                                         replay job's progress, not a
                                         fabricated percentage -- this
                                         process cannot observe that
                                         job while it runs, only what
                                         it has persisted so far.

Explicitly NOT emitted: "Analytics Progress". AnalyticsService
(app/backend/services/analytics_service.py) computes synchronously,
on demand, per REST request -- there is no background job with
persisted, incremental state to report progress on between requests.
Fabricating one here would break the same no-fake-numbers rule the
rest of this project follows. If a persisted/incremental analytics
job is ever built, its progress would get wired in here.
"""

import logging
from datetime import datetime, timedelta

from app.backend import settings
from app.backend.services import scanner_service, health_service, replay_service, error_handling, watchdog

log = logging.getLogger("trading_system.backend.live_service")

# Part 10: per-component consecutive-failure tracker shared by
# compute_diff_events(), build_full_snapshot() and poll_loop(). See
# error_handling.py's module docstring for why this was added -- it
# lets one source's failure (e.g. replay_service) be isolated from
# the other three instead of aborting the whole poll cycle.
_failures = error_handling.ComponentFailureTracker()

# Part 10: watchdog for poll_loop's background task -- see
# watchdog.py's module docstring. _effective_interval is a
# single-item list used as a mutable box so poll_loop can update the
# "currently in effect" interval (base or backed-off) and
# get_watchdog_status() can read it without a global statement.
_watchdog = watchdog.CycleWatchdog()
_effective_interval = [settings.WS_BROADCAST_INTERVAL_SECONDS]


def get_failure_status() -> dict:
    """
    Observability hook (Part 10). Surfaced through
    health_service.get_health()'s "live_pipeline" key as of this pass;
    also usable directly by a future dedicated route.
    """
    return _failures.get_status()


def get_watchdog_status() -> dict:
    """Observability hook (Part 10). See get_failure_status()."""
    return _watchdog.check(_effective_interval[0])


def _today() -> str:
    import state as state_mod
    return state_mod.trading_date_str()


def _week_monday(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def _year_month(date_str: str) -> str:
    return date_str[:7]


def _replay_progress_snapshot(today: str) -> dict:
    week_monday = _week_monday(today)
    year_month = _year_month(today)
    journal = replay_service.get_journal(today)
    daily = replay_service.get_daily_summary(today)
    weekly = replay_service.get_weekly_summary(today)
    monthly = replay_service.get_monthly_summary(year_month)
    return {
        "date": today,
        "journal_entries_today": journal.get("count", 0),
        "daily_summary_available": daily.get("available", False),
        "weekly_summary_available": weekly.get("available", False),
        "monthly_summary_available": monthly.get("available", False),
        "week_start": week_monday,
        "month": year_month,
    }


class _LastSnapshot:
    """
    Process-local, single-instance cursor for the diff loop. Only
    poll_loop's single background task ever mutates this (one call to
    compute_diff_events() runs to completion, awaited, before the next
    one starts), so no lock is needed. Does NOT back build_full_snapshot()
    -- that function is independent on purpose (see its docstring) so
    a client connecting doesn't reset what other connected clients are
    diffed against.
    """

    def __init__(self):
        self.scanner_status = None
        self.health = None
        self.trades_by_id = {}
        self.replay_progress = None


_last = _LastSnapshot()


def _build_trade_events(new_trades: dict) -> list:
    events = []
    old_trades = _last.trades_by_id

    new_ids = [tid for tid in new_trades if tid not in old_trades]
    if new_ids:
        events.append({"type": "new_signals", "data": [new_trades[tid] for tid in new_ids]})

    changed = [
        trade
        for tid, trade in new_trades.items()
        if tid in old_trades and old_trades[tid].get("status") != trade.get("status")
    ]
    if changed:
        events.append({"type": "trade_update", "data": changed})

    return events


def compute_diff_events() -> list:
    """
    Runs one poll cycle. Calls the same services the REST API uses (so
    results are always consistent with what /api/... would return
    right now), compares against the last cycle's snapshot, and
    returns only the pieces that changed as a list of
    {"type": ..., "data": ...} messages ready to broadcast.

    Blocking -- state_reader can trigger a git pull inside these
    calls. Callers must run this via asyncio.to_thread, never await it
    directly on the event loop.

    Part 10 fault isolation: each of the four sources below is run
    through error_handling.safe_call() independently. If one source
    raises, that source's event is skipped for this cycle (its _last
    cached value is left untouched, so the next successful cycle still
    diffs correctly) and the other three sources are still evaluated
    and broadcast normally -- a failure in, say, replay_service can no
    longer prevent a scanner_status or system_health update from
    reaching clients. See error_handling.py for the full rationale.
    """
    events = []
    today = _today()

    status, err = error_handling.safe_call(
        "scanner_status", "live_service", "compute_diff_events",
        scanner_service.get_status, today,
    )
    if err is None:
        _failures.record_success("scanner_status")
        if status != _last.scanner_status:
            events.append({"type": "scanner_status", "data": status})
            _last.scanner_status = status
    else:
        _failures.record_failure("scanner_status", err)

    health, err = error_handling.safe_call(
        "system_health", "live_service", "compute_diff_events",
        health_service.get_health,
    )
    if err is None:
        _failures.record_success("system_health")
        if health != _last.health:
            events.append({"type": "system_health", "data": health})
            _last.health = health
    else:
        _failures.record_failure("system_health", err)

    trades_by_id, err = error_handling.safe_call(
        "trades", "live_service", "compute_diff_events",
        scanner_service.get_trades_by_id, today,
    )
    if err is None:
        _failures.record_success("trades")
        events.extend(_build_trade_events(trades_by_id))
        _last.trades_by_id = trades_by_id
    else:
        _failures.record_failure("trades", err)

    if replay_service.is_enabled():
        progress, err = error_handling.safe_call(
            "replay_progress", "live_service", "compute_diff_events",
            _replay_progress_snapshot, today,
        )
        if err is None:
            _failures.record_success("replay_progress")
            if progress != _last.replay_progress:
                events.append({"type": "replay_progress", "data": progress})
                _last.replay_progress = progress
        else:
            _failures.record_failure("replay_progress", err)

    return events


def build_full_snapshot() -> list:
    """
    Called once, right after a client connects, so it sees the current
    state immediately instead of waiting for the next change. Reads
    fresh via the same services as compute_diff_events() but does NOT
    touch the shared _last cursor, so it can't reset another connected
    client's diff baseline or cause the poll loop to re-broadcast
    "new" events to everyone just because someone else connected.

    Also blocking, for the same reason as compute_diff_events().
    """
    today = _today()
    events = []

    status, err = error_handling.safe_call(
        "scanner_status", "live_service", "build_full_snapshot",
        scanner_service.get_status, today,
    )
    if err is None:
        _failures.record_success("scanner_status")
        events.append({"type": "scanner_status", "data": status})
    else:
        _failures.record_failure("scanner_status", err)

    health, err = error_handling.safe_call(
        "system_health", "live_service", "build_full_snapshot",
        health_service.get_health,
    )
    if err is None:
        _failures.record_success("system_health")
        events.append({"type": "system_health", "data": health})
    else:
        _failures.record_failure("system_health", err)

    trades_by_id, err = error_handling.safe_call(
        "trades", "live_service", "build_full_snapshot",
        scanner_service.get_trades_by_id, today,
    )
    if err is None:
        _failures.record_success("trades")
        if trades_by_id:
            events.append({"type": "new_signals", "data": list(trades_by_id.values())})
    else:
        _failures.record_failure("trades", err)

    if replay_service.is_enabled():
        progress, err = error_handling.safe_call(
            "replay_progress", "live_service", "build_full_snapshot",
            _replay_progress_snapshot, today,
        )
        if err is None:
            _failures.record_success("replay_progress")
            events.append({"type": "replay_progress", "data": progress})
        else:
            _failures.record_failure("replay_progress", err)

    return events


async def poll_loop(manager) -> None:
    """
    Background task started at app startup (see api/app.py), cancelled
    at shutdown. This is the entire "poll-and-diff" side of the Phase
    3 decision -- it never touches main.py or trader.yml. `manager` is
    an app.backend.api.ws_manager.ConnectionManager (passed in rather
    than imported, so this module has no import-time dependency on the
    API layer).
    """
    import asyncio

    base_interval = settings.WS_BROADCAST_INTERVAL_SECONDS
    # Part 10 graceful degradation: only back off once a component has
    # failed this many cycles in a row (a single blip shouldn't slow
    # everyone else down), and never sleep more than this multiple of
    # the configured interval. Resets to base_interval automatically
    # the moment every component succeeds again (worst_streak() -> 0).
    BACKOFF_AFTER_N_FAILURES = 3
    MAX_BACKOFF_MULTIPLIER = 4
    cycle_timeout = settings.WS_CYCLE_TIMEOUT_SECONDS

    while True:
        _watchdog.cycle_started()
        try:
            events = await asyncio.wait_for(
                asyncio.to_thread(compute_diff_events), timeout=cycle_timeout,
            )
            for event in events:
                await manager.broadcast(event)
            _watchdog.cycle_completed()
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            # Part 10 timeout protection: the cycle (most likely a
            # hung git operation inside state_reader) is abandoned
            # here -- the underlying thread cannot be forcibly killed,
            # so it keeps running and its eventual result is simply
            # discarded, but the loop itself is no longer blocked on
            # it. See watchdog.py's module docstring.
            _watchdog.cycle_timed_out()
            log.critical(
                "live_service poll cycle exceeded %ss -- possible frozen thread "
                "(e.g. a hung state branch git pull inside state_reader). "
                "Abandoning this cycle and continuing; if this repeats, check "
                "the dashboard process's network/git connectivity.",
                cycle_timeout,
            )
        except Exception:
            # Safety net only. compute_diff_events() isolates each of
            # its four sources internally (error_handling.safe_call),
            # so this branch catches genuinely unexpected failures --
            # e.g. inside manager.broadcast() itself -- not a single
            # source's read, which is why it's rarely hit in practice.
            log.exception("live_service poll cycle failed")

        streak = _failures.worst_streak()
        if streak >= BACKOFF_AFTER_N_FAILURES:
            multiplier = min(MAX_BACKOFF_MULTIPLIER, 1 + (streak - BACKOFF_AFTER_N_FAILURES + 1))
            sleep_for = base_interval * multiplier
            log.warning(
                "live_service poll: a component has failed %d consecutive cycles -- "
                "backing off to %ss this cycle (base=%ss)",
                streak, sleep_for, base_interval,
            )
        else:
            sleep_for = base_interval
        _effective_interval[0] = sleep_for
        await asyncio.sleep(sleep_for)
