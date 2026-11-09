"""Intraday P&L snapshot (Telegram text): closed trades (net of the assumed cost) + open trades marked to the last price.

One lot per trade, shadow data, before real slippage. Slots are fixed clock times; at most ONE message per run (the latest
due slot) so a gap in runs never produces a burst. Pure functions: sending/state marking is done by the callers.
"""
import eod_report

OPEN_STATUSES = ("ACTIVE", "T1_HIT")
NSE_SLOTS = ((11, 15), (13, 15), (15, 15))
MCX_SLOTS = ((11, 0), (13, 0), (15, 0), (17, 0), (19, 0), (21, 0))


def due_slot(now, slots, sent: list):
    """Latest slot at/before `now` not yet sent -> 'HHMM', else None. Caller records it (and earlier due slots) as sent."""
    due = [s for s in slots if (now.hour, now.minute) >= s]
    if not due:
        return None
    key = f"{due[-1][0]:02d}{due[-1][1]:02d}"
    return None if key in sent else key


def mark_sent(now, slots, sent: list):
    for s in slots:
        if (now.hour, now.minute) >= s:
            k = f"{s[0]:02d}{s[1]:02d}"
            if k not in sent:
                sent.append(k)


def summarize(rows: list, group_of) -> dict:
    """rows: eod_report.collect() rows. -> {group: {closed_n, wins, net, gross, open_n, mtm}} plus 'ALL'."""
    out = {}
    for r in rows:
        if r.get("twin_of_live"):
            continue
        t, status = r["t"], r["status"]
        g = out.setdefault(group_of(r), {"closed_n": 0, "wins": 0, "net": 0, "gross": 0, "open_n": 0, "mtm": 0})
        if status in OPEN_STATUSES:
            lot, entry, last = r.get("lot"), t.get("tracked_entry_price"), t.get("last_price")
            if lot and entry and last is not None:
                g["open_n"] += 1
                g["mtm"] += round((last - entry) * lot)
        elif r["pnl_gross"] is not None:
            g["closed_n"] += 1
            g["wins"] += 1 if r["pnl_gross"] > 0 else 0
            g["gross"] += r["pnl_gross"]
            g["net"] += r["pnl_net"] or 0
    tot = {"closed_n": 0, "wins": 0, "net": 0, "gross": 0, "open_n": 0, "mtm": 0}
    for g in out.values():
        for k in tot:
            tot[k] += g[k]
    out["ALL"] = tot
    return out


def _rs(v) -> str:
    return f"Rs {v:+,.0f}"


def format_message(title: str, hhmm: str, summary: dict) -> str:
    tot = summary["ALL"]
    lines = [f"P&L snapshot {hhmm[:2]}:{hhmm[2:]} IST - {title}",
             "One lot per trade, shadow data, before real slippage.", "",
             f"Closed: {tot['closed_n']} trades ({tot['wins']} wins), net {_rs(tot['net'])} (gross {_rs(tot['gross'])})",
             f"Open: {tot['open_n']} trades, mark-to-market {_rs(tot['mtm'])}",
             f"Total now (closed net + open): {_rs(tot['net'] + tot['mtm'])}", ""]
    for name, g in sorted(((k, v) for k, v in summary.items() if k != "ALL"), key=lambda kv: -(kv[1]["net"] + kv[1]["mtm"])):
        lines.append(f"{name}: closed {g['closed_n']} net {_rs(g['net'])} | open {g['open_n']} MTM {_rs(g['mtm'])}")
    return "\n".join(lines)


def nse_message(state: dict, hhmm: str) -> str:
    rows = eod_report.collect(state, eod_report.load_lot_sizes(state.get("date")))
    return format_message("NSE", hhmm, summarize(rows, lambda r: r["engine"]))


def mcx_message(state: dict, hhmm: str) -> str:
    import mcx_config as mc
    rows = eod_report.collect(state, mc.LOT_MULTIPLIER, engines=(("mcx_shadow_trades", "MCX"),))
    return format_message("MCX", hhmm, summarize(rows, lambda r: mc.NAMES.get(r["t"].get("symbol"), r["t"].get("symbol"))))
