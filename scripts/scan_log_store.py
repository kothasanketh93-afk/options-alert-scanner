"""
scan_log_store.py
------------------
Centralized, persistent, 5-trading-day troubleshooting log retention for
the scanner (2026-08-12).

WHY THIS EXISTS: logging_setup.py's own docstring explains why this
project writes logs to stdout only, not disk files -- every GitHub
Actions run is an ephemeral container, so on-disk files vanish the
moment the job ends unless something persists them. That module's
bottom section notes an artifact-upload alternative and explicitly
says it wasn't worth doing "until you actually need to grep historical
logs across many past runs" -- this is exactly that need, so this
module adds a SEPARATE persistence layer without changing
logging_setup.py, or any scoring/gating code, at all.

HOW: a logging.Handler (ScanLogCaptureHandler below) attached to the
SAME root "trading_system" logger logging_setup.py already configures
-- it runs alongside the existing stdout handler, capturing every log
line from every channel (scanner/tracker/api/telegram/main) without
touching a single line of scanner.py, filters.py, or trade_score.py.
Captured records are appended to a JSONL file per IST trading date,
stored in the SAME git branch (`state`, see state.py's own docstring
for why: a dedicated branch is this repo's existing, already-proven
centralized-storage mechanism -- no new database, no new service).

Retention: only the most recent config.SCAN_LOG_RETENTION_TRADING_DAYS
(default 5) TRADING days are kept (weekends/NSE holidays never
consumed a slot in the first place, since the scanner never runs on
those days at all -- holiday_guard.should_run() already prevents it --
but cleanup_old_trading_day_logs() below still filters by
holiday_guard.is_trading_day() defensively rather than assuming every
present file is automatically a valid trading day).

Export is entirely separate from retention: export_logs() below is
read-only against the stored files (never deletes or modifies them)
and is only ever invoked explicitly -- nothing in the automatic
per-run path calls it.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from state import STATE_CLONE_DIR, STATE_BRANCH, _run, _safe_pull, _git_identity, ist_now, trading_date_str
import holiday_guard

log = logging.getLogger("trading_system.scan_log_store")

IST = timezone(timedelta(hours=5, minutes=30))

SCAN_LOG_DIR = "scan_logs"

# Best-effort extraction of candidate identity from a log MESSAGE (not
# from any structured object passed in -- deliberately, since scanner.py
# itself is not touched by this module at all; see module docstring).
# Matches the "SYMBOL DIRECTION MONEYNESS strike=NNNN" shape that
# appears identically in scanner.py's own DIAG/GATE/SCORE/REJECTED/
# EXTENSION_CHECK/etc. log lines (confirmed against this session's own
# reviewed log output) -- e.g. "NIFTY CE ITM strike=24350.0". SYMBOL can
# contain spaces (e.g. "MULTI COMMODITY EXCHANGE"), so the pattern
# anchors on the DIRECTION/MONEYNESS/strike= tail rather than assuming a
# single-word symbol.
_CANDIDATE_PATTERN = re.compile(
    r"^(?P<symbol>.+?)\s+(?P<direction>CE|PE)\s+(?P<moneyness>ITM|ATM|OTM)\s+strike=(?P<strike>[\d.]+)"
)
_EXPIRY_PATTERN = re.compile(r"expiry=(?P<expiry>\d{4}-\d{2}-\d{2})")
_TAG_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]{2,})\b")


def _extract_candidate_fields(message: str) -> dict:
    """
    Returns {"candidate_id", "underlying", "direction", "strike",
    "moneyness", "expiry"} when the log message matches the known
    "SYMBOL DIRECTION MONEYNESS strike=N ..." shape (optionally with
    expiry=YYYY-MM-DD elsewhere in the same line), or {} if it doesn't
    (e.g. BIAS/NEWS_*/heartbeat lines that aren't about one specific
    candidate). candidate_id is deterministic and stable across scan
    cycles for the SAME contract (symbol+direction+strike+expiry) --
    this is what satisfies "traceable using a stable ID across 5-minute
    scan cycles": every cycle that evaluates the same strike produces
    the same candidate_id again, with no dependency on scanner.py
    assigning one itself (it doesn't, for rejected candidates -- only
    state.py's next_trade_id() numbers ones that actually get alerted).

    The leading all-caps TAG (REJECTED/GATE/SCORE/DIAG/CONTRACT_RESOLUTION/
    etc., same word _TAG_PATTERN above extracts) is stripped first --
    without this, `.+?` would happily match "REJECTED NIFTY" as the
    symbol instead of just "NIFTY", since it's lazy but still satisfies
    the rest of the pattern either way.
    """
    tag_m = _TAG_PATTERN.match(message)
    body = message[tag_m.end():].lstrip() if tag_m else message
    m = _CANDIDATE_PATTERN.search(body)
    if not m:
        return {}
    expiry_m = _EXPIRY_PATTERN.search(message)
    expiry = expiry_m.group("expiry") if expiry_m else None
    fields = {
        "underlying": m.group("symbol"),
        "direction": m.group("direction"),
        "moneyness": m.group("moneyness"),
        "strike": m.group("strike"),
        "expiry": expiry,
    }
    fields["candidate_id"] = "{}:{}:{}:{}".format(
        fields["underlying"], fields["direction"], fields["strike"], expiry or "?"
    )
    return fields


class ScanLogCaptureHandler(logging.Handler):
    """
    Attach to logging.getLogger("trading_system") (the SAME root logger
    logging_setup.py's own StreamHandler is already attached to) for the
    duration of one main.py run, then detach. Every record emitted by
    ANY "trading_system.*" channel during that window is captured here,
    structured, and available via .records for scan_log_store.flush_to_store()
    to persist -- the existing stdout handler is untouched and keeps
    working exactly as before; this is a second, independent handler on
    the same logger, not a replacement.
    """

    def __init__(self, scan_cycle_id: str):
        super().__init__(level=logging.INFO)
        self.scan_cycle_id = scan_cycle_id
        self.records = []

    def emit(self, record: logging.LogRecord):
        try:
            message = record.getMessage()
        except Exception:
            message = "<unformattable log record>"
        created = datetime.fromtimestamp(record.created, tz=timezone.utc)
        tag_m = _TAG_PATTERN.match(message)
        entry = {
            "timestamp_utc": created.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp_ist": created.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
            "scan_cycle_id": self.scan_cycle_id,
            "logger": record.name,
            "level": record.levelname,
            "tag": tag_m.group(1) if tag_m else None,
            "message": message,
        }
        entry.update(_extract_candidate_fields(message))
        self.records.append(entry)


def _scan_log_file_path(date_str: str) -> Path:
    return STATE_CLONE_DIR / SCAN_LOG_DIR / f"{date_str}.jsonl"


def flush_to_store(records: list, date_str: str = None, max_retries: int = 3):
    """
    Appends `records` (a list of dicts, e.g. ScanLogCaptureHandler(...).records
    from one run) as JSONL lines to today's scan_logs/<date_str>.jsonl,
    committing and pushing to the SAME `state` branch state.py already
    uses. Called ONCE per run with that run's own records only -- never
    reads or rewrites earlier lines, so a restart mid-run simply means
    the NEXT successful run's flush appends its own records on top of
    whatever was already committed; nothing already persisted is ever
    lost or duplicated (satisfies "safe against duplicate runs/restarts").

    No-ops cleanly if `records` is empty (nothing to append, nothing to
    commit) -- callers don't need to check first.
    """
    if not records:
        return
    date_str = date_str or trading_date_str()
    path = _scan_log_file_path(date_str)
    path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        _safe_pull(STATE_CLONE_DIR)

        with open(path, "a") as f:
            for entry in records:
                f.write(json.dumps(entry, default=str) + "\n")

        _run(["git", "add", "-A"], cwd=STATE_CLONE_DIR)
        commit_result = _run(
            ["git", "commit", "-m", f"scan logs {date_str} ({len(records)} entries)"],
            cwd=STATE_CLONE_DIR, check=False,
        )
        if commit_result.returncode != 0:
            if "nothing to commit" in commit_result.stdout:
                return
            log.error("scan log commit failed: %s", commit_result.stderr.strip())
            return

        push_result = _run(["git", "push", "origin", STATE_BRANCH], cwd=STATE_CLONE_DIR, check=False)
        if push_result.returncode == 0:
            return
        log.warning(
            "scan log push failed (attempt %d/%d): %s -- retrying after re-pull.",
            attempt + 1, max_retries, push_result.stderr.strip(),
        )
    log.error("scan log push failed after %d attempts -- today's captured records for this "
              "run were appended locally but could not be pushed; the NEXT successful run's "
              "pull+append will still include them as long as this local clone survives.",
              max_retries)


def cleanup_old_trading_day_logs(today_date_str: str = None, max_trading_days: int = None):
    """
    Deletes scan_logs/<date>.jsonl files other than the most recent
    `max_trading_days` TRADING days (config.SCAN_LOG_RETENTION_TRADING_DAYS
    by default -- see that config value's own comment). Mirrors state.py's
    cleanup_old_state_files() exactly: deletes from disk only, does NOT
    commit/push here -- flush_to_store()'s own `git add -A` + commit +
    push (called right after this, same run) picks up these deletions
    together with the day's new log entries, as one atomic commit (same
    reasoning as that function's own docstring: a separate premature
    commit here risks being silently discarded if a later pull-conflict
    recovery has to hard-reset before flush_to_store()'s own commit).

    Trading-day filtering (not just "keep N most recent files") is what
    satisfies "weekends/holidays must not consume a retention slot": a
    file is only eligible to occupy one of the `max_trading_days` slots
    if holiday_guard.is_trading_day() agrees its filename date actually
    was a trading day -- a file that doesn't parse as a valid trading
    date is left alone (never guessed at, never deleted opportunistically).
    """
    max_trading_days = max_trading_days or _retention_days()
    today_date_str = today_date_str or trading_date_str()
    log_dir = STATE_CLONE_DIR / SCAN_LOG_DIR
    if not log_dir.exists():
        return

    trading_day_files = []
    for f in log_dir.glob("*.jsonl"):
        try:
            parsed = datetime.strptime(f.stem, "%Y-%m-%d").replace(tzinfo=IST)
        except ValueError:
            continue  # not a dated log file -- leave it alone, never guess
        if not holiday_guard.is_trading_day(parsed):
            continue  # defensive: shouldn't normally happen (scanner never runs on non-trading days)
        trading_day_files.append((f.stem, f))

    trading_day_files.sort(key=lambda pair: pair[0], reverse=True)  # newest first
    for date_str, f in trading_day_files[max_trading_days:]:
        try:
            f.unlink()
        except OSError:
            pass  # best-effort, same posture as cleanup_old_state_files()


def _retention_days() -> int:
    try:
        import config
        return config.SCAN_LOG_RETENTION_TRADING_DAYS
    except (ImportError, AttributeError):
        return 5


def run_retention_and_flush(records: list, date_str: str = None):
    """
    The single call main.py makes once per run, after scanning: enforces
    5-trading-day retention (deleting anything older) and appends this
    run's captured records -- both picked up together in ONE commit by
    flush_to_store()'s own git operations. This is the only function this
    module expects to run automatically; export_logs() below is never
    called from here or anywhere in the automatic per-run path.
    """
    date_str = date_str or trading_date_str()
    cleanup_old_trading_day_logs(date_str)
    flush_to_store(records, date_str)


# ---------------------------------------------------------------------------
# On-demand export -- READ-ONLY against the stored files. Never called
# automatically; only when explicitly invoked (function call, or this
# module's own CLI below).
# ---------------------------------------------------------------------------

def _iter_stored_dates() -> list:
    log_dir = STATE_CLONE_DIR / SCAN_LOG_DIR
    if not log_dir.exists():
        return []
    dates = []
    for f in log_dir.glob("*.jsonl"):
        try:
            datetime.strptime(f.stem, "%Y-%m-%d")
        except ValueError:
            continue
        dates.append(f.stem)
    return sorted(dates)


def export_logs(
    output_path,
    trading_date: str = None,
    date_start: str = None,
    date_end: str = None,
    underlying: str = None,
    candidate_id: str = None,
    only_rejected: bool = False,
    only_eligible: bool = False,
):
    """
    Combines selected retained scan-log records into ONE file at
    `output_path` (a JSON list of record dicts). Read-only against the
    stored .jsonl files -- opens them "r" only, never "w"/"a", and never
    deletes anything; safe to call repeatedly with different filters
    without any risk to the centralized store.

    Filters (all optional, combine with AND when more than one is given):
      trading_date: exact "YYYY-MM-DD" -- just that one day.
      date_start/date_end: inclusive "YYYY-MM-DD" range (either or both).
      underlying: exact symbol match (e.g. "NIFTY").
      candidate_id: exact "SYMBOL:DIRECTION:STRIKE:EXPIRY" match (see
          ScanLogCaptureHandler's docstring for how this is derived) --
          pulls every line across every retained day for one specific
          contract's full evaluation history.
      only_rejected / only_eligible: filter by tag == "REJECTED" or a
          final-eligibility tag ("CALIBRATED_PROBABILITY" with
          status=AVAILABLE, or an alert-sent marker) -- mutually
          exclusive; only_eligible takes precedence if both are True.

    No filters at all -> every retained record across every day still
    in the 5-trading-day window.

    Returns the number of records written.
    """
    dates = [trading_date] if trading_date else _iter_stored_dates()
    if date_start or date_end:
        dates = [d for d in dates if (not date_start or d >= date_start) and (not date_end or d <= date_end)]

    combined = []
    for date_str in dates:
        path = _scan_log_file_path(date_str)
        if not path.exists():
            continue
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if underlying and entry.get("underlying") != underlying:
                    continue
                if candidate_id and entry.get("candidate_id") != candidate_id:
                    continue
                if only_eligible and entry.get("tag") != "CALIBRATED_PROBABILITY":
                    continue
                if only_eligible and "AVAILABLE" not in entry.get("message", ""):
                    continue
                if (not only_eligible) and only_rejected and entry.get("tag") != "REJECTED":
                    continue
                combined.append(entry)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(combined, f, indent=2, default=str)
    return len(combined)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="On-demand export of centralized scanner troubleshooting logs.")
    parser.add_argument("--output", required=True, help="Path to write the combined export JSON file")
    parser.add_argument("--date", help="Exact trading date YYYY-MM-DD")
    parser.add_argument("--date-start", help="Range start YYYY-MM-DD (inclusive)")
    parser.add_argument("--date-end", help="Range end YYYY-MM-DD (inclusive)")
    parser.add_argument("--underlying", help="Filter to one underlying symbol")
    parser.add_argument("--candidate-id", help="Filter to one SYMBOL:DIRECTION:STRIKE:EXPIRY")
    parser.add_argument("--only-rejected", action="store_true")
    parser.add_argument("--only-eligible", action="store_true")
    args = parser.parse_args()

    count = export_logs(
        args.output, trading_date=args.date, date_start=args.date_start, date_end=args.date_end,
        underlying=args.underlying, candidate_id=args.candidate_id,
        only_rejected=args.only_rejected, only_eligible=args.only_eligible,
    )
    print(f"Exported {count} records to {args.output}")
