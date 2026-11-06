"""
MCX shadow runner (added 2026-11-05) -- Crude Oil, Natural Gas, Gold, Gold Mini OPTIONS, SHADOW ONLY (no alerts, no orders).

Separate from the NSE trader on purpose: own workflow (mcx_trader.yml, own concurrency group), own state and ledger
under mcx/ on the `state` branch, own cron-job.org schedule. Reuses the pure sweep/FVG/AMD/OI-flow signal functions
(app/engines/sweep_reversal/signals.py, fixed rules, never tuned on results) and tracker.process_trade_candles.

Run: python scripts/mcx_main.py   (every 5 minutes, 09:00-23:30 IST, Mon-Fri)
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth
import state as state_mod
import tracker
import outcomes_ledger
import telegram_notify as tg
import mcx_report
import shadow_notify
from angelone import AngelOneBroker
from app.engines.sweep_reversal import signals
from indicators import compute_atr
from logging_setup import get_logger
from trade_record import new_trade_record, TERMINAL_STATUSES
from exceptions import BrokerAPIError

log = get_logger("mcx_main")
IST = state_mod.IST

# --- MCX settings (fixed rules; a change = a new strategy name, not a tweak) -----------------------------------
from mcx_config import COMMODITIES, LOT_MULTIPLIER, NAMES, STRATEGY_LABELS
SESSION_OPEN = (9, 0)
SESSION_CLOSE = (23, 59)             # market closes 23:30; the extra window lets a late-dispatched 23:30 run still close trades and report
REPORT_AT = (23, 28)                   # daily report (and Friday weekly report) go out once, at/after this time
TRADE_CLOSE_HHMM = (23, 25)            # tracker closes open trades at/after this (last full 5-min bar)
LAST_SIGNAL_MINS = 22 * 60 + 30        # no new setups in the last hour
MIN_EXPIRY_DAYS = 1                    # skip the option expiry day itself
MAX_SPREAD_PCT = 4.0
MIN_PREMIUM_VOLUME = 20
ATM_DELTA_PROXY = 0.5                  # no greeks feed for MCX; ATM delta ~0.5 (recorded in each trade)
FLOW_RING = 3
STRATEGIES = ("sweep", "sweep_fvg", "amd", "fvg", "oi_flow")

# The NSE-hour constants inside signals are patched for this process only (the MCX day is 09:00-23:30).
signals.LAST_SIGNAL_MINS = LAST_SIGNAL_MINS
signals.FLOW_LAST_MINS = LAST_SIGNAL_MINS
signals.FLOW_FIRST_MINS = 9 * 60 + 30

MCX_DIR = "mcx"


def _state_path(date_str: str) -> Path:
    return state_mod.STATE_CLONE_DIR / MCX_DIR / "state" / f"{date_str}.json"


def load_state(date_str: str) -> dict:
    p = _state_path(date_str)
    if p.exists():
        try:
            st = json.load(open(p))
            st.setdefault("mcx_shadow_trades", {})
            st.setdefault("next_seq", {})
            st.setdefault("flow_snap", {})
            st.setdefault("rejects", {})
            return st
        except (json.JSONDecodeError, OSError):
            p.rename(p.with_suffix(f".corrupted-{datetime.now(IST).strftime('%H%M%S')}.json"))
    return {"date": date_str, "mcx_shadow_trades": {}, "next_seq": {}, "flow_snap": {}, "rejects": {}, "summary_sent": False}


def save_state(st: dict, msg: str):
    state_mod._state_file_path = _state_path          # save_state() writes through this path helper
    state_mod.save_state(st, msg)


def in_session(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return SESSION_OPEN <= (now.hour, now.minute) <= SESSION_CLOSE


# --- contract resolution ---------------------------------------------------------------------------------------
def _exp(row) -> datetime:
    return datetime.strptime(row["expiry"], "%d%b%Y")


def resolve_contracts(master: list, name: str, today: datetime):
    """Nearest future + nearest option expiry (>= MIN_EXPIRY_DAYS away) for one commodity, or None."""
    d0 = today.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    futs = sorted([x for x in master if x.get("exch_seg") == "MCX" and x.get("name") == name
                   and x.get("instrumenttype") == "FUTCOM" and _exp(x) >= d0], key=_exp)
    opts = [x for x in master if x.get("exch_seg") == "MCX" and x.get("name") == name and x.get("instrumenttype") == "OPTFUT"
            and _exp(x) >= d0 + timedelta(days=MIN_EXPIRY_DAYS)]
    if not futs or not opts:
        return None
    oexp = min(_exp(x) for x in opts)
    chain = [x for x in opts if _exp(x) == oexp]
    for x in chain:
        x["_strike"] = float(x["strike"]) / 100.0
    # opt_fut = the future the options settle against (first expiry on/after the option expiry): its live price decides ATM.
    opt_fut = next((f for f in futs if _exp(f) >= oexp), futs[0])
    return {"fut": futs[0], "futs": futs[:3], "opt_fut": opt_fut, "chain": chain, "expiry": oexp.strftime("%Y-%m-%d"),
            "lot": int(float(chain[0].get("lotsize") or 1))}


def pick_signal_future(broker, st: dict, name: str, c: dict) -> dict:
    """Candles for pattern detection come from the MOST LIQUID of the first three futures (highest open interest), fixed for
    the day once chosen. The nearest contract is not always the liquid one (2026-11-05: GOLDM Dec traded in thin, flat bars
    while GOLDM Jan was the real market, which produced a false FVG signal)."""
    chosen = st.setdefault("sig_fut", {})
    tok = chosen.get(name)
    futs = c.get("futs") or [c["fut"]]
    if tok:
        hit = next((f for f in futs if f["token"] == tok), None)
        if hit:
            return hit
    try:
        qs = broker._batch_quotes("MCX", [f["token"] for f in futs])
        best = max(futs, key=lambda f: float((qs.get(f["token"]) or {}).get("opnInterest") or 0))
        if float((qs.get(best["token"]) or {}).get("opnInterest") or 0) <= 0:
            best = c["opt_fut"]
    except BrokerAPIError:
        best = c["opt_fut"]
    chosen[name] = best["token"]
    log.info("MCX %s signal future = %s (opt future %s)", name, best["symbol"], c["opt_fut"]["symbol"])
    return best


def _leg(chain, strike, direction):
    kind = "CE" if direction == "CE" else "PE"
    for x in chain:
        if abs(x["_strike"] - strike) < 1e-6 and x["symbol"].endswith(kind):
            return x
    return None


def _quote_fields(q: dict) -> dict:
    d = q.get("depth") or {}
    bid = (d.get("buy") or [{}])[0].get("price")
    ask = (d.get("sell") or [{}])[0].get("price")
    return {"ltp": q.get("ltp"), "bid": bid, "ask": ask, "oi": q.get("opnInterest"), "volume": q.get("tradeVolume")}


# --- detection -------------------------------------------------------------------------------------------------
def scan_commodity(broker, st: dict, name: str, c: dict, now: datetime):
    today_str = st["date"]
    sig_fut = pick_signal_future(broker, st, name, c)
    fut_key = f"MCX|{sig_fut['token']}"
    since = (now - timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    allc = broker.get_candles(fut_key, "5minute", since_iso=since)
    today = [x for x in allc if x["timestamp"][:10] == today_str]
    if len(today) < 4:
        return
    elapsed = (now.hour * 60 + now.minute) - (SESSION_OPEN[0] * 60 + SESSION_OPEN[1])
    if elapsed >= 30 and len(today) < 0.6 * (elapsed // 5):          # thin / gappy series: patterns on it are not trustworthy
        key = f"{name}:sparse_candles"
        st["rejects"][key] = st["rejects"].get(key, 0) + 1
        log.warning("MCX %s: only %d of ~%d 5m candles on %s -- skipping scan.", name, len(today), elapsed // 5, sig_fut["symbol"])
        return
    atr = compute_atr(allc[-60:])
    if not atr or atr <= 0:
        return
    daily = broker.get_daily_candles(fut_key, 5)
    prev = daily[-1] if daily else None
    ph, pl = (prev["high"], prev["low"]) if prev else (None, None)

    found = {"sweep": signals.detect_sweep(today, ph, pl), "sweep_fvg": signals.detect_sweep_fvg(today, ph, pl),
             "amd": signals.detect_amd(today, ph, pl), "fvg": signals.detect_fvg(today, atr)}
    # OI-flow snapshot (ATM +- FLOW_RING strikes) every run
    spot = today[-1]["close"]
    strikes = sorted({x["_strike"] for x in c["chain"]})
    opt_q = broker._batch_quotes("MCX", [c["opt_fut"]["token"]]).get(c["opt_fut"]["token"], {})
    spot_opt = opt_q.get("ltp") or spot
    atm = min(strikes, key=lambda s: abs(s - spot_opt))
    i = strikes.index(atm)
    ring = strikes[max(0, i - FLOW_RING): i + FLOW_RING + 1]
    toks = [x["token"] for x in c["chain"] if x["_strike"] in ring]
    qs = broker._batch_quotes("MCX", toks)
    call_oi = sum(float(qs[x["token"]].get("opnInterest") or 0) for x in c["chain"] if x["_strike"] in ring and x["symbol"].endswith("CE") and x["token"] in qs)
    put_oi = sum(float(qs[x["token"]].get("opnInterest") or 0) for x in c["chain"] if x["_strike"] in ring and x["symbol"].endswith("PE") and x["token"] in qs)
    snaps = st["flow_snap"].setdefault(name, [])
    snaps.append({"t": now.hour * 60 + now.minute, "spot": spot, "call_oi": call_oi, "put_oi": put_oi})
    del snaps[:-80]
    found["oi_flow"] = signals.detect_oi_flow(snaps, atr)

    for strategy, sig in found.items():
        if not sig:
            continue
        if any(t["strategy"] == strategy and t["symbol"] == name for t in st["mcx_shadow_trades"].values()):
            continue
        create_trade(broker, st, name, c, strategy, sig, atr, ph, pl, qs, now, spot_opt)


def create_trade(broker, st, name, c, strategy, sig, atr, ph, pl, qs, now, spot_opt=None):
    direction = sig["direction"]
    spot = spot_opt or sig["signal_close"]      # ATM comes from the options' own underlying, never from the signal series
    strikes = sorted({x["_strike"] for x in c["chain"]})
    atm = min(strikes, key=lambda s: abs(s - spot))
    leg = _leg(c["chain"], atm, direction)
    if not leg:
        return
    q = qs.get(leg["token"]) or broker._batch_quotes("MCX", [leg["token"]]).get(leg["token"], {})
    qf = _quote_fields(q)
    prem = qf["ltp"]
    why = None
    if not prem or prem <= 0:
        why = "no_premium"
    elif not qf["bid"] or not qf["ask"]:
        why = "no_two_sided_quote"
    else:
        spread = (qf["ask"] - qf["bid"]) / prem * 100
        if spread > MAX_SPREAD_PCT:
            why = f"spread_{spread:.1f}pct"
        elif (qf["volume"] or 0) < MIN_PREMIUM_VOLUME:
            why = "low_volume"
    if why:
        key = f"{name}:{why}"
        st["rejects"][key] = st["rejects"].get(key, 0) + 1
        log.info("MCX_REJECTED %s %s %s: %s", strategy, name, direction, why)
        return
    lv = signals.option_levels(prem, ATM_DELTA_PROXY, sig["signal_close"], sig["extreme"], atr, direction)
    rr = (lv["target_1"] - prem) / (prem - lv["stop_loss"]) if prem > lv["stop_loss"] else 0.0
    spread_pct = round((qf["ask"] - qf["bid"]) / prem * 100, 2)
    setup = {
        "strategy": strategy, "symbol": name, "direction": direction, "moneyness": "ATM",
        "instrument_key": f"MCX|{leg['token']}", "strike": atm, "expiry": c["expiry"], "spot_price": spot,
        "alert_premium": prem, "entry_low": prem, "entry_high": prem,
        "stop_loss": lv["stop_loss"], "target_1": lv["target_1"], "target_2": lv["target_2"],
        "grade": "Shadow", "trade_score": 50, "probability_pct": None, "probability_label": "Shadow",
        "score_breakdown": [], "confidence_score": 5.0, "risk_score": round(min(10.0, rr * 3), 1),
        "reward_score": round(min(10.0, rr * 4), 1), "expected_rr": f"1:{rr:.1f}", "factor_checklist": [],
        "confidence_stars": "", "confidence_label": "Shadow", "risk_rating": "", "risk_label": "Unvalidated",
        "expected_holding_minutes": [30, 180], "why_reasons": [], "session": "MCX", "alternatives": [],
        "bid": qf["bid"], "ask": qf["ask"], "spread_pct": spread_pct,
    }
    key = f"MCX-{strategy}-{name}"
    seq = st["next_seq"].get(key, 0) + 1
    st["next_seq"][key] = seq
    trade_id = f"{key}-{direction}-{now.strftime('%Y%m%d-%H%M')}-{seq:02d}"
    t = new_trade_record(setup, trade_id, now, f"MCX {len(st['mcx_shadow_trades']) + 1}")
    t.update({"probability_status": "shadow_only", "execution_mode": "shadow", "live_alert_sent": False,
              "engine": "mcx", "market_close_hhmm": list(TRADE_CLOSE_HHMM), "delta_proxy": ATM_DELTA_PROXY,
              "lot_multiplier": LOT_MULTIPLIER[name], "level_name": sig.get("level_name"),
              "sweep_extreme": sig.get("extreme"), "fvg": sig.get("fvg"),
              "entry_oi": qf["oi"], "entry_volume": qf["volume"], "signal_future_token": (st.get("sig_fut") or {}).get(name)})
    st["mcx_shadow_trades"][trade_id] = t
    notify_created(t)
    log.info("MCX_SHADOW_CANDIDATE_CREATED %s %s %s strike=%s prem=%s sl=%s t1=%s", strategy, name, direction, atm, prem, lv["stop_loss"], lv["target_1"])


def _inr(x) -> str:
    return f"{x:+,.0f}"


def notify_created(t: dict):
    try:
        mult = t.get("lot_multiplier") or LOT_MULTIPLIER.get(t["symbol"], 1)
        e, sl, t1, t2 = t["alert_premium"], t["stop_loss"], t["target_1"], t["target_2"]
        tg.send(f"🧪 <b>MCX SHADOW</b> – data collection, NOT a trade signal\n"
                f"<b>{NAMES.get(t['symbol'], t['symbol'])} {t['direction']} {t['strike']:g}</b> ({STRATEGY_LABELS.get(t['strategy'], t['strategy'])})\n"
                f"Premium {e} | SL {sl} | T1 {t1} | T2 {t2}\n"
                f"One lot: risk to SL Rs {abs((sl - e) * mult):,.0f}, T1 +Rs {(t1 - e) * mult:,.0f}, T2 +Rs {(t2 - e) * mult:,.0f}"
                + (f"\nSpread {t['spread_pct']}%" if t.get("spread_pct") is not None else ""))
    except Exception as exc:
        log.error("MCX created notice failed (%s: %s)", type(exc).__name__, exc)


def notify_events(t: dict, events: list):
    mult = t.get("lot_multiplier") or LOT_MULTIPLIER.get(t["symbol"], 1)
    for ev in events:
        et = ev.get("type")
        if et not in shadow_notify.OUTCOME_LABEL:
            continue
        try:
            entry = t.get("tracked_entry_price")
            res = outcomes_ledger.result_pct(t) if et in ("SL_HIT", "T2_HIT", "EOD_CLOSED") else None
            pnl = f"\nOne lot result: Rs {_inr(entry * mult * res / 100.0)} before costs" if res is not None and entry else ""
            tg.send(f"🧪 MCX SHADOW\n{shadow_notify.OUTCOME_LABEL[et]}: <b>{NAMES.get(t['symbol'], t['symbol'])} {t['direction']} {t['strike']:g}</b> "
                    f"({STRATEGY_LABELS.get(t['strategy'], t['strategy'])})\nEntry {entry} | SL {t['stop_loss']} | T1 {t['target_1']} | T2 {t['target_2']}{pnl}")
        except Exception as exc:
            log.error("MCX outcome notice failed (%s: %s)", type(exc).__name__, exc)


def track_open(broker, st: dict):
    for tid, t in st["mcx_shadow_trades"].items():
        if t["status"] in TERMINAL_STATUSES:
            continue
        try:
            candles = broker.get_candles(t["instrument_key"], "1minute", since_iso=t["last_processed_ts"])
            if candles:
                notify_events(t, tracker.process_trade_candles(t, candles))
        except BrokerAPIError as exc:
            log.error("MCX tracker %s failed (%s) -- retry next run.", tid, exc)


# --- end-of-day summary ----------------------------------------------------------------------------------------
def pnl_rows(st: dict):
    rows = []
    for t in st["mcx_shadow_trades"].values():
        res = outcomes_ledger.result_pct(t)
        if res is None or not t.get("tracked_entry_price"):
            continue
        entry = t["tracked_entry_price"]
        mult = t.get("lot_multiplier") or LOT_MULTIPLIER.get(t["symbol"], 1)
        gross = entry * mult * res / 100.0
        cost = entry * mult * outcomes_ledger.config.LEDGER_ROUND_TRIP_COST_PCT / 100.0
        rows.append({"symbol": t["symbol"], "strategy": t["strategy"], "status": t.get("final_status") or t["status"],
                     "gross": gross, "net": gross - cost, "res": res})
    return rows


def summary_text(st: dict) -> str:
    rows = pnl_rows(st)
    if not rows:
        return f"MCX shadow summary {st['date']}: no closed trades today."
    out = [f"<b>MCX shadow summary {st['date']}</b> (one lot per trade, after est. costs)"]
    for name in COMMODITIES:
        rs = [r for r in rows if r["symbol"] == name]
        if rs:
            wins = sum(1 for r in rs if r["gross"] > 0)
            out.append(f"{name}: {len(rs)} trades, {wins} wins, net Rs {sum(r['net'] for r in rs):,.0f}")
    out.append(f"<b>Total net Rs {sum(r['net'] for r in rows):,.0f}</b> on {len(rows)} trades")
    if st.get("rejects"):
        out.append("Skipped (liquidity): " + ", ".join(f"{k} x{v}" for k, v in sorted(st["rejects"].items())))
    return "\n".join(out)


def send_reports(st: dict, now: datetime):
    """Once a day at/after REPORT_AT: daily one-lot image (text fallback); Fridays also the weekly image."""
    if (now.hour, now.minute) < REPORT_AT:
        return
    import tempfile
    ledger = state_mod.STATE_CLONE_DIR / MCX_DIR / "ledger" / "outcomes.csv"
    if not st.get("summary_sent"):
        png = os.path.join(tempfile.mkdtemp(), f"mcx_daily_{st['date']}.png")
        if mcx_report.render_daily(st, png):
            tg.send_photo(png, caption=f"MCX daily trade report {st['date']} (one lot per trade, shadow data)")
        else:
            tg.send(f"MCX shadow report {st['date']}: no trades today.")
        st["summary_sent"] = True
    if now.weekday() == 4 and not st.get("weekly_sent"):
        png = os.path.join(tempfile.mkdtemp(), f"mcx_weekly_{st['date']}.png")
        data = mcx_report.render_weekly(ledger, st["date"], png)
        if data:
            tg.send_photo(png, caption=f"MCX weekly trade report {data['start']} to {data['end']} (one lot per trade, shadow data)")
        st["weekly_sent"] = True


def main():
    now = state_mod.ist_now()
    if os.environ.get("MCX_FORCE") != "1" and not in_session(now):
        log.info("Outside MCX session (%s) -- nothing to do.", now.strftime("%a %H:%M"))
        return 0
    state_mod.ensure_state_branch_checked_out(os.environ["REPO_URL"], auth.get_github_token())
    date_str = now.strftime("%Y-%m-%d")
    st = load_state(date_str)
    broker = AngelOneBroker()
    broker.login()
    master = broker._scrip_master()
    err = None
    try:
        for name in COMMODITIES:
            try:
                c = resolve_contracts(master, name, now)
                if not c:
                    log.warning("MCX %s: no contracts resolved", name)
                    continue
                if (now.hour * 60 + now.minute) <= LAST_SIGNAL_MINS + 5:
                    scan_commodity(broker, st, name, c, now)
            except BrokerAPIError as exc:
                log.error("MCX %s scan failed (%s: %s) -- continuing.", name, type(exc).__name__, exc)
        track_open(broker, st)
    except Exception as exc:       # save progress, then re-raise
        err = exc
        log.error("MCX run error (%s: %s)", type(exc).__name__, exc, exc_info=True)
    try:
        outcomes_ledger.STATE_KEYS = tuple(outcomes_ledger.STATE_KEYS) + (("mcx_shadow_trades", "mcx"),)
        added = outcomes_ledger.append_resolved(st, state_mod.STATE_CLONE_DIR / MCX_DIR / "ledger" / "outcomes.csv")
        if added:
            log.info("MCX ledger appended %d trade(s)", added)
    except Exception as exc:
        log.error("MCX ledger append failed (%s: %s) -- continuing.", type(exc).__name__, exc)
    try:
        send_reports(st, now)
    except Exception as exc:
        log.error("MCX report failed (%s: %s) -- continuing.", type(exc).__name__, exc)
    save_state(st, f"MCX shadow state {date_str} {now.strftime('%H:%M')}")
    if err:
        raise err
    return 0


if __name__ == "__main__":
    sys.exit(main())
