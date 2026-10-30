"""
trade_lifecycle.py
-------------------
ALERTS ONLY. This module never places a trade, never modifies a broker
position, and never force-closes anything -- it is a pure read/derive/
label layer on top of the trade dicts main.py (state.py) already
tracks and persists.

It does three things:
  1. Derives one flat "lifecycle_status" label (the vocabulary below)
     from whatever granular internal status tracker.py already
     computed -- that module remains the only code that decides ACTUAL
     trade outcomes (target/SL/EOD detection). This module never
     overrides trade["status"]; it only reads it.
  2. Tracks the extra display/analytics fields the lifecycle spec asks
     for (current price, current PnL, running max profit/drawdown,
     holding time) as plain keys on the SAME trade dict -- no new
     schema, no new file.
  3. Applies the ~15:25 IST "OPEN AT EOD" label to still-open trades,
     via a boolean flag on the trade dict -- never touches
     trade["status"], never exits a position. tracker.py's OWN 15:30
     EOD_CLOSED transition (unmodified, still the only thing that
     actually stops per-cycle tracking of a trade) still runs exactly
     as before.

Status vocabulary (trade_lifecycle.lifecycle_status()'s return value):
    OPEN, TARGET HIT, SL HIT, OPEN AT EOD, CLOSED, EXPIRED, CANCELLED

Persistence: NONE of its own. Every field this module writes lives on
the same trade dict that state.py's save_state() already serializes to
the `state` git branch every run -- so lifecycle data survives a
restart for free, with zero new storage code.

(BTST/STBT support -- a separate overnight-carry status path keyed by
an `is_btst` flag on lifecycle_status()/to_dashboard_record() -- was
removed on 2026-10-30 at the user's explicit request, along with the
standalone btst_*.py module it only ever served. Everything below is
unchanged for the intraday path.)
"""

from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

STATUS_OPEN = "OPEN"
STATUS_TARGET_HIT = "TARGET HIT"
STATUS_SL_HIT = "SL HIT"
STATUS_OPEN_AT_EOD = "OPEN AT EOD"
STATUS_CLOSED = "CLOSED"
STATUS_EXPIRED = "EXPIRED"
STATUS_CANCELLED = "CANCELLED"

# tracker.py internal statuses that are still live -- no target/SL/close
# reached yet. Mirrors state.py's OPEN_STATUSES (T1_HIT included -- a
# trade that hit T1 and is still tracking T2 is still open) but kept as
# its own tuple here rather than importing state.py's, so this read-only
# module has zero dependency on state.py's git/branch machinery.
_LIVE_INTRADAY = {"WAITING_ENTRY", "ACTIVE", "T1_HIT"}


def _parse_iso(ts):
    if not ts:
        return None
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


def _is_expired(trade: dict, now: datetime) -> bool:
    """True if the option contract's own expiry date has already
    passed while the trade is still sitting in a live status -- an
    orphaned/never-resolved trade, not a normal EOD close."""
    expiry = trade.get("expiry")
    if not expiry:
        return False
    try:
        expiry_date = (
            datetime.fromisoformat(expiry).date() if "T" in expiry
            else datetime.strptime(expiry, "%Y-%m-%d").date()
        )
    except (ValueError, TypeError):
        return False
    return now.date() > expiry_date


def lifecycle_status(trade: dict, now: datetime = None) -> str:
    """
    Pure function: reads trade["status"] (set by tracker.py) and a
    couple of lifecycle-only flags this module itself sets
    (_marked_open_at_eod, cancelled), and returns one of the vocabulary
    strings above. Never mutates `trade`, never calls a broker.
    """
    now = now or datetime.now(IST)

    if trade.get("cancelled"):
        return STATUS_CANCELLED

    internal = trade.get("status")

    if internal in _LIVE_INTRADAY:
        if _is_expired(trade, now):
            return STATUS_EXPIRED
        if trade.get("_marked_open_at_eod"):
            return STATUS_OPEN_AT_EOD
        return STATUS_OPEN
    if internal == "T2_HIT":
        return STATUS_TARGET_HIT
    if internal == "SL_HIT":
        return STATUS_SL_HIT
    if internal == "NOT_ENTERED":
        return STATUS_CANCELLED
    if internal in ("EOD_CLOSED", "AMBIGUOUS"):
        return STATUS_CLOSED
    return STATUS_CLOSED  # safe default for any status this module doesn't recognize


def update_lifecycle_metrics(trade: dict, current_price: float, now: datetime = None) -> None:
    """
    Call once per scan cycle (Monitoring, every cycle) for every
    non-terminal trade, using a price the caller already fetched this
    cycle (e.g. the last 1-min candle's close tracker.py just
    processed) -- this function makes NO broker call of its own, so it
    adds zero API/rate-limit load.

    Pure bookkeeping: sets current_price / current_pnl_pct /
    maximum_profit_pct / maximum_drawdown_pct / holding_time_minutes
    on `trade` in place. Never touches trade["status"], never closes
    or exits anything.
    """
    now = now or datetime.now(IST)
    entry_price = trade.get("tracked_entry_price") or trade.get("alert_premium")

    trade["current_price"] = current_price

    if entry_price:
        pnl_pct = round((current_price - entry_price) / entry_price * 100, 2)
        trade["current_pnl_pct"] = pnl_pct
        trade["maximum_profit_pct"] = max(trade.get("maximum_profit_pct", 0.0) or 0.0, pnl_pct)
        trade["maximum_drawdown_pct"] = min(trade.get("maximum_drawdown_pct", 0.0) or 0.0, pnl_pct)
    else:
        trade["current_pnl_pct"] = None

    entry_time = _parse_iso(trade.get("actual_entry_time") or trade.get("alert_time"))
    if entry_time:
        trade["holding_time_minutes"] = int((now - entry_time).total_seconds() // 60)


def mark_open_at_eod(state: dict, now: datetime = None) -> int:
    """
    The "Around 3:25 PM" EOD check. Intended to be called once per run
    from main.py, gated by `now >= config.EOD_STATUS_CHECK_HHMM`
    (caller's responsibility, same pattern as maybe_send_daily_summary()
    already uses for SUMMARY_HHMM). Idempotent -- safe to call every
    remaining cycle of the day; only sets the flag once per trade.

    Scans every trade currently in a live status; if neither target
    nor SL has been reached (i.e. it's still WAITING_ENTRY/ACTIVE/
    T1_HIT), flags it so lifecycle_status() reports "OPEN AT EOD".
    NEVER sets trade["status"] itself, NEVER exits/closes/modifies a
    position -- purely a display label alongside the existing status.

    Returns the count of trades newly marked this call, for logging.
    """
    now = now or datetime.now(IST)
    marked = 0
    for trade in state.get("trades", {}).values():
        if trade.get("_marked_open_at_eod"):
            continue
        if trade.get("status") in _LIVE_INTRADAY:
            trade["_marked_open_at_eod"] = True
            marked += 1
    return marked


def to_dashboard_record(trade: dict, now: datetime = None) -> dict:
    """
    Flat view using the exact field names the Trade Lifecycle spec
    asks for -- for display/API/export use. Read-only, does not
    mutate `trade`.
    """
    targets = [t for t in (trade.get("target_1"), trade.get("target_2")) if t is not None]
    entry = trade.get("tracked_entry_price")
    if entry is None:
        lo, hi = trade.get("entry_low"), trade.get("entry_high")
        entry = f"{lo}-{hi}" if lo is not None and hi is not None else lo

    return {
        "Trade ID": trade.get("trade_id") or trade.get("position_id"),
        "Entry": entry,
        "SL": trade.get("stop_loss"),
        "Targets": targets,
        "Probability": trade.get("probability_pct"),
        "Trade Score": trade.get("trade_score"),
        "Strategy": trade.get("strategy"),
        "Current Price": trade.get("current_price"),
        "Current PnL": trade.get("current_pnl_pct"),
        "Maximum Profit": trade.get("maximum_profit_pct"),
        "Maximum Drawdown": trade.get("maximum_drawdown_pct"),
        "Holding Time": trade.get("holding_time_minutes"),
        "Status": lifecycle_status(trade, now=now),
    }
