"""BankNifty performance statistics + the daily (end-of-day) and weekly Telegram texts.

Stats use NET P&L (after charges and slippage estimate) for ONE lot. Live alerts and all BankNifty setups (live + shadow data) are
reported separately because only live alerts are what a real account would have taken.
The edge check is deliberately strict: an edge is "not proven" until there are >=100 entered trades over >=20 trading days AND the lower end of a
day-block bootstrap 90% confidence interval on the mean net P&L per trade is above zero.
"""
import random
from html import escape

MIN_TRADES_FOR_EDGE = 100
MIN_DAYS_FOR_EDGE = 20   # trading days; the paper phase (Dec) is what can reach this


def _n(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def entered(rows):
    return [r for r in rows if _n(r.get("net_pnl_rs")) is not None]


def stats(rows) -> dict:
    """Summary over log rows (entered trades only count toward P&L stats)."""
    alerts = len(rows)
    ts = sorted(entered(rows), key=lambda r: (str(r.get("exit_time") or ""), str(r.get("alert_time") or "")))
    pnl = [_n(r["net_pnl_rs"]) for r in ts]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    cum = peak = dd = 0.0
    for p in pnl:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    gl = -sum(losses)
    return {"alerts": alerts, "trades": len(pnl), "not_entered": alerts - len(pnl), "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(pnl) * 100, 1) if pnl else None,
            "avg_win": round(sum(wins) / len(wins)) if wins else None, "avg_loss": round(sum(losses) / len(losses)) if losses else None,
            "net": round(sum(pnl)), "expectancy": round(sum(pnl) / len(pnl)) if pnl else None,
            "profit_factor": round(sum(wins) / gl, 2) if gl > 0 else None, "max_drawdown": round(dd),
            "best": max(ts, key=lambda r: _n(r["net_pnl_rs"])) if ts else None,
            "worst": min(ts, key=lambda r: _n(r["net_pnl_rs"])) if ts else None, "pnl": pnl,
            "by_day": [(str(r.get("date")), _n(r["net_pnl_rs"])) for r in ts]}


def edge_check(by_day, n_boot=4000, seed=11) -> dict:
    """Day-block bootstrap of mean net P&L per trade (whole trading days are resampled, because trades within a day -- and ITM/ATM/OTM
    variants of one idea -- are strongly correlated; resampling single trades would overstate confidence). `by_day` = [(date, pnl)]."""
    if not by_day:
        return {"n": 0, "days": 0, "verdict": "no trades yet"}
    days = {}
    for d, p in by_day:
        days.setdefault(d, []).append(p)
    groups, rng = list(days.values()), random.Random(seed)
    means = []
    for _ in range(n_boot):
        pick = rng.choices(groups, k=len(groups))
        tot = sum(len(g) for g in pick)
        means.append(sum(sum(g) for g in pick) / tot)
    means.sort()
    lo, hi = means[int(0.05 * n_boot)], means[int(0.95 * n_boot) - 1]
    n, nd = len(by_day), len(groups)
    if n < MIN_TRADES_FOR_EDGE or nd < MIN_DAYS_FOR_EDGE:
        verdict = f"NOT PROVEN - {n} trades over {nd} days (need {MIN_TRADES_FOR_EDGE}+ trades and {MIN_DAYS_FOR_EDGE}+ days)"
    elif lo > 0:
        verdict = "POSITIVE - lower bound above zero"
    else:
        verdict = "NOT PROVEN - confidence range includes zero"
    return {"n": n, "days": nd, "mean": round(sum(p for _, p in by_day) / n), "ci_low": round(lo), "ci_high": round(hi), "verdict": verdict}


def _rs(v):
    return "-" if v is None else f"{'+' if v > 0 else ''}{v:,.0f}"


def _block(title, s):
    if not s["alerts"]:
        return f"<b>{escape(title)}</b>: none"
    line = (f"<b>{escape(title)}</b>: {s['alerts']} alerts, {s['trades']} entered"
            + (f", win {s['win_rate']}% ({s['wins']}W/{s['losses']}L)" if s["trades"] else ""))
    if s["trades"]:
        line += (f"\n  avg win {_rs(s['avg_win'])} | avg loss {_rs(s['avg_loss'])} | net <b>{_rs(s['net'])}</b> | "
                 f"max drawdown {_rs(-s['max_drawdown'])}")
        b, w = s["best"], s["worst"]
        line += (f"\n  best {_rs(_n(b['net_pnl_rs']))} ({escape(str(b.get('strategy')))} {escape(str(b.get('option_type')))} "
                 f"{escape(str(b.get('strike')))}), worst {_rs(_n(w['net_pnl_rs']))} ({escape(str(w.get('strategy')))})")
    return line


def _latency_line(rows):
    v = sorted(x for x in (_n(r.get("signal_to_send_sec")) for r in rows) if x is not None)
    d = sorted(x for x in (_n(r.get("detect_to_send_sec")) for r in rows) if x is not None)
    if not v and not d:
        return None
    med = lambda a: a[len(a) // 2]
    parts = []
    if d:
        parts.append(f"scan->sent median {med(d):.0f}s, max {d[-1]:.0f}s")
    if v:
        parts.append(f"candle close->sent median {med(v)/60:.1f} min, max {v[-1]/60:.1f} min")
    return "Latency: " + "; ".join(parts)


def daily_text(day_rows, date_str, all_rows) -> str:
    """End-of-day BankNifty summary: today's numbers + running total since the log began."""
    live = [r for r in day_rows if r.get("kind") == "live"]
    out = [f"🏦 <b>BankNifty daily summary {escape(date_str)}</b> (1 lot, net of costs)", _block("Live alerts", stats(live)),
           _block("All BankNifty setups (live + data)", stats(day_rows))]
    lat = _latency_line(day_rows)
    if lat:
        out.append(lat)
    live_all = [r for r in all_rows if r.get("kind") == "live"]
    cs = stats(live_all)
    if cs["trades"]:
        out.append(f"Live since start: {cs['trades']} trades, net {_rs(cs['net'])}, win {cs['win_rate']}%, max DD {_rs(-cs['max_drawdown'])}")
    return "\n".join(out)


def weekly_text(week_rows, start, end, all_rows) -> str:
    live = [r for r in week_rows if r.get("kind") == "live"]
    out = [f"🏦 <b>BankNifty weekly report {escape(start)} to {escape(end)}</b> (1 lot, net of costs)",
           _block("Live alerts", stats(live)), _block("All BankNifty setups (live + data)", stats(week_rows))]
    lat = _latency_line(week_rows)
    if lat:
        out.append(lat)
    for title, rows in (("Live", [r for r in all_rows if r.get("kind") == "live"]), ("All setups", all_rows)):
        s = stats(rows)
        e = edge_check(s["by_day"])
        if e["n"]:
            out.append(f"<b>Edge check - {title}</b>: {e['n']} trades / {e['days']} days, mean {_rs(e['mean'])}/trade "
                       f"(90% range {_rs(e['ci_low'])} to {_rs(e['ci_high'])}) -> {escape(e['verdict'])}")
        else:
            out.append(f"<b>Edge check - {title}</b>: no trades yet")
    return "\n".join(out)
