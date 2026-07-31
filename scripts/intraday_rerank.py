"""
intraday_rerank.py -- Phase 6: intraday dynamic re-ranking of SCAN_UNIVERSE.
------------------------------------------------------------------------------
Extends Phase 1 (opportunity_score.py / build_opportunity_shortlist.py,
which still run ONCE premarket and produce the day's FIRST SCAN_UNIVERSE)
with periodic intraday re-scoring, so a MASTER_UNIVERSE stock that has
gone cold (or hot) since the open can be dropped from (added to)
SCAN_UNIVERSE automatically -- no manual watchlist edit, ever.

CALLED FROM: main.py, once per run, right after config.refresh_symbols_
for_today() and BEFORE run_scanner_step() -- so if this run's clock time
falls inside one of config.RERANK_TIMES, that run's option-chain scan
already uses the freshly re-ranked SCAN_UNIVERSE, same run.

WHAT THIS DOES NOT DO: it never touches option chains, Greeks, ATM/ITM/
OTM selection, entry/target/SL, probability, or Telegram trade alerts --
those all still belong to scanner.py/option_chain.py/trade_score.py,
completely unchanged. This module only decides WHICH symbols
config.STOCK_SYMBOLS contains before scanner.py runs. It reuses
opportunity_score.build_shortlist() exactly as-is (same lightweight
inputs: batch quotes, daily candles, one OI-summation call per
survivor -- no option-chain/Greeks/strike work), so a rerank costs the
same, bounded API budget the premarket run already costs, not a second
full option-scan pass.

UNIVERSE SAFETY: build_shortlist() only ever draws from
universe.MASTER_UNIVERSE (or, if config.USE_DYNAMIC_FNO_UNIVERSE is
True, the broker's live F&O-eligible list intersected with the
exchange's actual tradable stock-options universe -- never an external
watchlist, never a hardcoded name). Re-running it intraday cannot
introduce any symbol that a premarket run couldn't already have
produced -- same function, same candidate pool, just called again with
today's live quotes.
"""

from datetime import datetime, timezone, timedelta

import config
import opportunity_score
import telegram_notify as tg
from daily_cache import DailyCache
from exceptions import TelegramFailure
from logging_setup import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
log = get_logger("intraday_rerank")


def _hhmm_to_minutes(hhmm: str) -> int:
    hh, mm = hhmm.split(":")
    return int(hh) * 60 + int(mm)


def _matching_slot(now: datetime) -> str | None:
    """
    Returns the RERANK_TIMES entry (e.g. "13:30") this run's clock time
    is within config.RERANK_WINDOW_MINUTES of, or None if it isn't near
    any configured rerank time. Picks the CLOSEST slot if somehow within
    range of more than one (only possible with a very short RERANK_TIMES
    spacing and a large window -- not the default).
    """
    now_min = now.hour * 60 + now.minute
    best_slot, best_delta = None, None
    for slot in config.RERANK_TIMES:
        try:
            slot_min = _hhmm_to_minutes(slot)
        except (ValueError, AttributeError):
            log.warning(f"RERANK_TIMES entry {slot!r} is not HH:MM -- skipping it.")
            continue
        delta = abs(now_min - slot_min)
        if delta <= config.RERANK_WINDOW_MINUTES and (best_delta is None or delta < best_delta):
            best_slot, best_delta = slot, delta
    return best_slot


def maybe_rerank(state: dict, now: datetime = None) -> dict | None:
    """
    Re-scores the full universe and, if this run's clock time matches a
    configured RERANK_TIMES slot that hasn't already been done today,
    replaces config.STOCK_SYMBOLS/SYMBOLS in place with the fresh
    top-TOP_STOCK_COUNT shortlist. Idempotent per (date, slot): state[
    "rerank_slots_done"] (persisted the same way every other per-day
    field in state.py already is) stops a slot being re-applied on a
    second run that also happens to land in its window (e.g. a manual
    workflow_dispatch re-run, or -- during the 15-minute lunch cadence
    -- two ticks both falling inside RERANK_WINDOW_MINUTES of the same
    slot).

    Best-effort throughout, same posture as build_opportunity_shortlist.
    py / config.refresh_symbols_for_today(): any failure is logged and
    swallowed, leaving SCAN_UNIVERSE exactly as it already was (today's
    premarket shortlist, or the last successful rerank) rather than
    ever leaving the scanner with zero symbols. Returns the diff dict
    ({"added": [...], "removed": [...], "slot": ...}) on a successful
    rerank, or None if no rerank happened this run (disabled, no
    matching slot, already done, or it failed).
    """
    if not config.ENABLE_INTRADAY_RERANK:
        return None

    now = now or datetime.now(IST)
    slot = _matching_slot(now)
    if slot is None:
        return None

    done = state.setdefault("rerank_slots_done", [])
    if slot in done:
        log.info(f"Rerank slot {slot} already applied today -- skipping.")
        return None

    before = list(config.STOCK_SYMBOLS)
    trading_date = now.strftime("%Y-%m-%d")

    try:
        payload = opportunity_score.build_shortlist(trading_date)
    except Exception as exc:
        log.error(
            f"Intraday rerank ({slot}) failed: {type(exc).__name__}: {exc} -- "
            f"keeping the current SCAN_UNIVERSE unchanged.",
            exc_info=True,
        )
        return None

    shortlist = payload.get("shortlist") or []
    if not shortlist:
        log.warning(f"Intraday rerank ({slot}) produced an empty shortlist -- "
                    f"keeping the current SCAN_UNIVERSE unchanged.")
        return None

    after = [row["symbol"] for row in shortlist]

    # Persist under today's date, same cache/slot build_opportunity_
    # shortlist.py's premarket run already writes -- overwriting it is
    # deliberate: config.refresh_symbols_for_today() (called first on
    # every run, including THIS one) always reads today's single latest
    # shortlist, so the next run picks up this rerank automatically with
    # no separate "which shortlist is current" logic needed anywhere else.
    ok = DailyCache("opportunity").write(
        trading_date, payload,
        commit_message=f"intraday_rerank: {trading_date} {slot} shortlist ({len(after)} stocks)",
    )
    if not ok:
        log.error(f"Intraday rerank ({slot}) computed but FAILED to persist -- "
                   f"applying it to THIS run only; the next run will fall back "
                   f"to whatever was last saved.")
        # Still apply in-process below -- this run's scan benefits even
        # if the cache write failed; only persistence across runs is lost.

    config.STOCK_SYMBOLS.clear()
    config.STOCK_SYMBOLS.extend(after)
    config.SYMBOLS.clear()
    config.SYMBOLS.extend(config.INDEX_SYMBOLS + config.STOCK_SYMBOLS)

    done.append(slot)

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    diff = {"slot": slot, "added": added, "removed": removed, "scan_universe": after}

    log.info(
        f"Intraday rerank ({slot}): SCAN_UNIVERSE refreshed -- "
        f"{len(added)} added, {len(removed)} removed, {len(after)} total. "
        f"Added: {added or '-'}. Removed: {removed or '-'}."
    )
    _notify_best_effort(diff)
    return diff


def _notify_best_effort(diff: dict):
    if not diff["added"] and not diff["removed"]:
        # Same top names re-qualified -- do not spam Telegram with a
        # no-op rerank notice every slot.
        return
    lines = [f"🔄 <b>SCAN_UNIVERSE re-ranked ({diff['slot']} IST)</b>"]
    if diff["added"]:
        lines.append(f"➕ Added: {', '.join(diff['added'])}")
    if diff["removed"]:
        lines.append(f"➖ Removed: {', '.join(diff['removed'])}")
    lines.append(f"Now scanning {len(diff['scan_universe'])} stocks (options-live).")
    try:
        tg.send("\n".join(lines))
    except TelegramFailure:
        log.error("Could not send intraday-rerank diff (Telegram unreachable): %s", diff)
