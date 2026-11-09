"""Intraday P&L snapshot (Telegram text): closed trades (net of the assumed cost) + open trades marked to the last price.

One lot per trade, shadow data, before real slippage. Slots are fixed clock times; at most ONE message per run (the latest
due slot) so a gap in runs never produces a burst. Pure functions: sending/state marking is done by the callers.
"""
import eod_report

OPEN_STATUSES = ("ACTIVE", "T1_HIT")
NSE_SLOTS = ((10, 15), (11, 15), (12, 15), (13, 15), (14, 15), (15, 15))      # hourly, 45-60 min after each hour starts
MCX_SLOTS = ((11, 0), (13, 0), (15, 0), (17, 0), (19, 0), (21, 0), (23, 0))     # every 2 hours


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


def open_positions(rows: list) -> list:
    """Open trades with a last price, as simple dicts for the image."""
    out = []
    for r in rows:
        t = r["t"]
        if r.get("twin_of_live") or r["status"] not in OPEN_STATUSES:
            continue
        lot, entry, last = r.get("lot"), t.get("tracked_entry_price"), t.get("last_price")
        if not (lot and entry and last is not None):
            continue
        strike = t.get("strike")
        strike = int(strike) if isinstance(strike, float) and strike == int(strike) else strike
        out.append({"name": f"{t.get('symbol')} {t.get('direction')} {strike}", "strategy": r["engine"] if not t.get("strategy") else
                    f"{r['engine']}" if r['engine'] == t.get("strategy") else f"{r['engine']}", "entry": entry, "last": last,
                    "pct": (last - entry) / entry * 100, "mtm": round((last - entry) * lot)})
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


def _nse(state, lot_sizes):
    rows = eod_report.collect(state, lot_sizes if lot_sizes is not None else eod_report.load_lot_sizes(state.get("date")))
    return rows, summarize(rows, lambda r: r["engine"])


def _mcx(state):
    import mcx_config as mc
    rows = eod_report.collect(state, mc.LOT_MULTIPLIER, engines=(("mcx_shadow_trades", "MCX"),))
    for r in rows:
        r["engine"] = mc.STRATEGY_LABELS.get(r["t"].get("strategy"), r["t"].get("strategy"))
    return rows, summarize(rows, lambda r: mc.NAMES.get(r["t"].get("symbol"), r["t"].get("symbol")))


def nse_message(state: dict, hhmm: str, lot_sizes: dict = None) -> str:
    return format_message("NSE", hhmm, _nse(state, lot_sizes)[1])


def mcx_message(state: dict, hhmm: str) -> str:
    return format_message("MCX", hhmm, _mcx(state)[1])


def send_snapshot(market: str, state: dict, hhmm: str, send_photo, send_text, lot_sizes: dict = None):
    """Image first (text fallback). market: 'NSE' or 'MCX'. Returns 'image' or 'text'."""
    import os
    import tempfile
    import pnl_snapshot_image
    rows, summary = _nse(state, lot_sizes) if market == "NSE" else _mcx(state)
    try:
        png = os.path.join(tempfile.mkdtemp(), f"pnl_{market}_{hhmm}.png")
        if pnl_snapshot_image.render(market, state.get("date", ""), hhmm, summary, open_positions(rows), png,
                                     "Engine" if market == "NSE" else "Commodity"):
            send_photo(png, caption=f"{market} P&L snapshot {hhmm[:2]}:{hhmm[2:]} IST (one lot per trade, shadow data)")
            return "image"
    except Exception:
        pass
    send_text(format_message(market, hhmm, summary))
    return "text"
