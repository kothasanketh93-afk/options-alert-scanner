"""End-of-day trade report (Telegram): every tracked trade today (live + shadow engines) grouped by outcome.

Educational/data tool, not investment advice. Pure formatting from state; sending is done by main.py once per day.
"""
from html import escape

import outcomes_ledger

_ENGINES = (("trades", "LIVE"), ("shadow_trades", "Main shadow"), ("fib_pivot_shadow_trades", "Fib pivot"),
            ("orb_pivot_shadow_trades", "ORB"), ("sweep_shadow_trades", "Sweep"))
_GROUPS = (
    ("win", "✅ Targets hit (T2)", ("T2_HIT",)),
    ("partial", "🟡 T1 hit, then stopped", ("T1_THEN_SL", "T1_THEN_TRAILING_SL", "T1_HIT_THEN_EOD", "T1_HIT")),
    ("loss", "❌ Stop-loss hit", ("SL_HIT", "TRAILING_SL_HIT")),
    ("other", "⏳ Open / closed at end of day / not entered", None),
)


def _fmt(x):
    return "-" if x is None else (f"{x:.2f}" if isinstance(x, (int, float)) else str(x))


_SWEEP_LABELS = {"sweep_fvg": "Sweep+FVG", "amd": "AMD", "oi_flow": "OI flow", "fvg": "FVG"}


def lot_sizes_from_rows(master_rows: list) -> dict:
    """{underlying_symbol: lot_size} from the instrument master's option rows."""
    out = {}
    for r in master_rows or []:
        if r.get("option_type") in ("CE", "PE") and r.get("lot_size") and r.get("underlying_symbol"):
            out.setdefault(r["underlying_symbol"], int(r["lot_size"]))
    return out


def lot_sizes_from_gz(path: str) -> dict:
    import gzip, json
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return lot_sizes_from_rows(json.load(f).get("rows", []))
    except Exception:
        return {}


def load_lot_sizes(trading_date: str = None) -> dict:
    """Lot sizes from today's cached instrument master (never downloads); {} on any failure -> P&L column shows '-'."""
    try:
        import instrument_master as im
        d = trading_date or im.today_str()
        rows = im._inprocess_master.get(d)
        if rows is None:
            cached = im._CACHE.read(d)
            rows = (cached or {}).get("rows", [])
        return lot_sizes_from_rows(rows)
    except Exception:
        return {}


def _exit_price(t, status):
    return {"T2_HIT": t.get("target_2"), "SL_HIT": t.get("stop_loss"), "T1_THEN_SL": t.get("stop_loss"),
            "T1_THEN_TRAILING_SL": t.get("stop_loss"), "TRAILING_SL_HIT": t.get("stop_loss"),
            "EOD_CLOSED": t.get("eod_last_price"), "T1_HIT_THEN_EOD": t.get("eod_last_price")}.get(status)


def collect(state: dict, lot_sizes: dict = None) -> list:
    """One dict per tracked trade. pnl_gross/pnl_net = rupees for ONE lot (net = minus the assumed round-trip cost)."""
    rows = []
    seen_keys = set()   # same contract + same alert time already counted under an earlier engine (LIVE first)
    for key, label in _ENGINES:
        for t in (state.get(key) or {}).values():
            status = t.get("final_status") or t.get("status")
            res = outcomes_ledger.result_pct(t)
            lot = (lot_sizes or {}).get(t.get("symbol"))
            entry, exit_ = t.get("tracked_entry_price"), _exit_price(t, status)
            gross = net = None
            if lot and entry and exit_ is not None:
                gross = round((exit_ - entry) * lot)
                net = round(gross - entry * lot * outcomes_ledger.config.LEDGER_ROUND_TRIP_COST_PCT / 100)
            _k = (t.get("symbol"), t.get("direction"), t.get("strike"), t.get("alert_time"))
            twin = _k in seen_keys
            seen_keys.add(_k)
            if key == "sweep_shadow_trades" and t.get("strategy") in _SWEEP_LABELS:
                label_row = _SWEEP_LABELS[t["strategy"]]
            else:
                label_row = label
            rows.append({"engine": label_row, "t": t, "status": status, "net": outcomes_ledger.net_result_pct(res),
                         "lot": lot, "pnl_gross": gross, "pnl_net": net, "twin_of_live": twin})
    return rows


def totals(rows: list) -> dict:
    """Closed-trade rupee totals for one lot each; a shadow copy of a live trade is not counted twice."""
    def agg(sel):
        g = [r["pnl_gross"] for r in sel if r["pnl_gross"] is not None and not r["twin_of_live"]]
        n = [r["pnl_net"] for r in sel if r["pnl_net"] is not None and not r["twin_of_live"]]
        return {"gross": sum(g), "net": sum(n), "n": len(g)}
    out = {"all": agg(rows)}
    for label in dict.fromkeys(r["engine"] for r in rows):
        out[label] = agg([r for r in rows if r["engine"] == label])
    return out


def _group_of(status):
    for gid, _title, members in _GROUPS:
        if members and status in members:
            return gid
    return "other"


def _line(r) -> str:
    t = r["t"]
    when = (t.get("alert_time") or "")[11:16]
    net = f" | net {r['net']:+.1f}%" if r["net"] is not None else ""
    return (f"• {escape(str(t.get('symbol')))} {escape(str(t.get('direction')))} {escape(str(t.get('strike')))} "
            f"[{r['engine']}, {when}] entry {_fmt(t.get('tracked_entry_price') or t.get('alert_premium'))}, "
            f"SL {_fmt(t.get('stop_loss'))}, T1 {_fmt(t.get('target_1'))}, T2 {_fmt(t.get('target_2'))} "
            f"→ {escape(str(r['status']))}{net}")


def build_messages(state: dict, date_str: str, limit: int = 3800) -> list:
    rows = collect(state)
    if not rows:
        return []
    by = {g[0]: [r for r in rows if _group_of(r["status"]) == g[0]] for g in _GROUPS}
    head = [f"📊 <b>End-of-day trade report {escape(date_str)}</b>",
            f"Tracked: {len(rows)} | T2: {len(by['win'])} | T1 then SL: {len(by['partial'])} | "
            f"SL: {len(by['loss'])} | open/other: {len(by['other'])}"]
    engines = []
    for _k, label in _ENGINES:
        er = [r for r in rows if r["engine"] == label]
        if er:
            engines.append(f"{label}: {sum(1 for r in er if _group_of(r['status']) == 'win')} T2, "
                           f"{sum(1 for r in er if _group_of(r['status']) == 'partial')} T1→SL, "
                           f"{sum(1 for r in er if _group_of(r['status']) == 'loss')} SL of {len(er)}")
    head.append("By engine – " + "; ".join(engines))
    lines = ["\n".join(head)]
    for gid, title, _m in _GROUPS:
        if by[gid]:
            lines.append(f"\n<b>{title}</b>")
            lines += [_line(r) for r in by[gid]]
    lines.append("\nNet % assumes round-trip costs. Shadow = data collection, not signals. "
                 "Educational only; not a SEBI-registered advisor.")
    msgs, cur = [], ""
    for ln in lines:
        if cur and len(cur) + len(ln) + 1 > limit:
            msgs.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + ln
    if cur:
        msgs.append(cur)
    return msgs
