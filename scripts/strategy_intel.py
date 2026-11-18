"""Strategy Intelligence (research mode, read-only).

Reads the outcomes ledgers and produces per strategy x instrument tables, cluster-deduplicated results and concentration checks.
It never touches live rules, brokers or Telegram. Rupee figures are 1 lot per trade, ideal fills unless stated.
Run: python strategy_intel.py <nse_outcomes.csv> [out_dir]
"""
import csv
import math
import sys
from pathlib import Path

LOTS = {"NIFTY": 65, "SENSEX": 20, "BANKNIFTY": 30, "MIDCPNIFTY": 120}   # BANKNIFTY / MIDCPNIFTY assumed
SKIP = {"NOT_ENTERED", "AMBIGUOUS"}


def _f(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def load_trades(path):
    """Normalised trades: one per trade_id, index options only, never mixing live with shadow."""
    out, seen = [], set()
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            tid = r.get("trade_id")
            if tid in seen or r.get("final_status") in SKIP:
                continue
            seen.add(tid)
            sym = r.get("symbol")
            net, ep = _f(r.get("net_result_pct")), _f(r.get("entry_price"))
            if sym not in LOTS or net is None or ep is None:
                continue
            real = _f(r.get("realistic_net_pct"))
            src = "live" if r.get("engine") == "live" else "shadow"
            at = r.get("alert_time") or ""
            out.append({"id": tid, "src": src, "strategy": r.get("strategy") or "", "symbol": sym, "dir": r.get("direction"),
                        "date": r.get("date"), "at": at, "hhmm": at[11:16], "expiry": str(r.get("expiry_day")).lower() == "true",
                        "net": net, "inr": net / 100 * ep * LOTS[sym],
                        "real_inr": (real / 100 * ep * LOTS[sym]) if real is not None else None,
                        "status": r.get("final_status")})
    return sorted(out, key=lambda t: (t["date"], t["at"]))


def session(t):
    h = t["hhmm"]
    return "09:15-10:35" if h < "10:35" else "10:35-11:35" if h < "11:35" else "11:35-13:45" if h < "13:45" else "13:45+"


def metrics(trades):
    n = len(trades)
    if not n:
        return None
    pnl = [t["inr"] for t in trades]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    gw, gl = sum(wins), -sum(losses)
    peak = cum = dd = streak = worst = 0.0
    for p in pnl:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        streak = streak + 1 if p <= 0 else 0
        worst = max(worst, streak)
    real = [t["real_inr"] for t in trades if t["real_inr"] is not None]
    return {"n": n, "win_pct": round(100 * len(wins) / n, 1), "net": round(sum(pnl)), "expectancy": round(sum(pnl) / n),
            "avg_win": round(gw / len(wins)) if wins else 0, "avg_loss": round(-gl / len(losses)) if losses else 0,
            "pf": round(gw / gl, 2) if gl else None, "max_dd": round(dd), "max_loss_streak": int(worst),
            "realistic_net": round(sum(real)) if len(real) == n else None}


def dedupe_clusters(trades):
    """One signal = one bet: keep the first trade per (date, strategy, symbol, direction). Same signal on other strikes is not new evidence."""
    seen, out = set(), []
    for t in sorted(trades, key=lambda t: t["at"]):
        k = (t["date"], t["strategy"], t["symbol"], t["dir"], t["src"])
        if k not in seen:
            seen.add(k)
            out.append(t)
    return sorted(out, key=lambda t: (t["date"], t["at"]))


def concentration(trades, top=5):
    """How much of the net result comes from the best few trades."""
    pnl = sorted((t["inr"] for t in trades), reverse=True)
    total = sum(pnl)
    return {"net": round(total), "top_n": top, "top_sum": round(sum(pnl[:top])), "net_without_top": round(total - sum(pnl[:top]))}


def group(trades, key):
    d = {}
    for t in trades:
        d.setdefault(key(t), []).append(t)
    return d


def report_rows(trades):
    """Rows for strategy x instrument (raw and deduplicated), live and shadow kept apart."""
    rows = []
    for (src, strat), g in sorted(group(trades, lambda t: (t["src"], t["strategy"])).items()):
        for sym in list(LOTS) + ["ALL"]:
            sel = g if sym == "ALL" else [t for t in g if t["symbol"] == sym]
            m, md = metrics(sel), metrics(dedupe_clusters(sel))
            if m:
                rows.append({"source": src, "strategy": strat, "symbol": sym, **m, "dedup_n": md["n"], "dedup_net": md["net"],
                             "dedup_win_pct": md["win_pct"]})
    return rows


def write_reports(trades, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = report_rows(trades)
    cols = list(rows[0]) if rows else []
    with open(out / "strategy_by_instrument.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    lines = ["# Strategy Intelligence (research mode)", "",
             "1 lot per trade, index options only, ideal fills unless noted. Shadow and live kept apart. Dedup = first trade per "
             "(day, strategy, instrument, direction): the same signal on other strikes is not new evidence.", "",
             "| Source | Strategy | Instr | Trades | Win% | Net Rs | PF | Max DD | Loss streak | Dedup trades | Dedup net Rs |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['source']} | {r['strategy']} | {r['symbol']} | {r['n']} | {r['win_pct']} | {r['net']:,} | {r['pf']} | {r['max_dd']:,} | {r['max_loss_streak']} | {r['dedup_n']} | {r['dedup_net']:,} |")
    lines += ["", "## Concentration (shadow, all trades of a strategy)", "", "| Strategy | Net Rs | Top-5 trades Rs | Net without top 5 |", "|---|---|---|---|"]
    for (src, strat), g in sorted(group([t for t in trades if t["src"] == "shadow"], lambda t: (t["src"], t["strategy"])).items()):
        c = concentration(g)
        lines.append(f"| {strat} | {c['net']:,} | {c['top_sum']:,} | {c['net_without_top']:,} |")
    lines += ["", "## Session and expiry (shadow, all strategies)", "", "| Cut | Trades | Win% | Net Rs |", "|---|---|---|---|"]
    sh = [t for t in trades if t["src"] == "shadow"]
    for label, key in (("session", session), ("expiry day", lambda t: "expiry" if t["expiry"] else "non-expiry")):
        for k, g in sorted(group(sh, key).items()):
            m = metrics(g)
            lines.append(f"| {label}: {k} | {m['n']} | {m['win_pct']} | {m['net']:,} |")
    (out / "strategy_intelligence.md").write_text("\n".join(lines) + "\n")
    return out / "strategy_intelligence.md"


if __name__ == "__main__":
    ledger = sys.argv[1]
    print(write_reports(load_trades(ledger), sys.argv[2] if len(sys.argv) > 2 else "intel_out"))
