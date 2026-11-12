"""Weekly strategy scorecard: every shadow strategy scored from the outcomes ledgers, as one Telegram image.

Score (0-100) = 40 net-per-trade after the assumed cost + 20 profit factor + 15 win rate + 15 winning-days share
+ 10 first-target rate, scaled by sqrt(trades/30) (full weight at 30 trades) so a thin sample cannot win on luck.
Provisional by design: one week is a regime sample, not proof of edge.
"""
import csv
import math
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

from eod_image import NAVY, INK, MUTED, LINE, GREEN, RED, AMBER, GREEN_BG, RED_BG, AMBER_BG, W, PAD, _font

LABELS = {"fib_pivot": "Fib pivot", "trade_score": "Main scanner (scored)", "chase_bypass": "Main scanner (chase bypass)",
          "live_scored": "Live alerts (scored)", "orb15": "ORB 15-min", "pivot_break": "Pivot break",
          "sweep": "Liquidity sweep", "sweep_fvg": "Sweep + FVG", "amd": "AMD", "oi_flow": "OI flow",
          "fvg": "Fair value gap", "ema_pb": "EMA pullback", "gap_orb": "Gap and go", "sess_orb": "Session ORB",
          "sess_sweep": "Session sweep", "bos": "Break of structure", "order_block": "Order block"}
SKIP = {"NOT_ENTERED", "AMBIGUOUS"}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def week_bounds(any_date: str):
    d = datetime.strptime(any_date, "%Y-%m-%d").date()
    mon = d - timedelta(days=d.weekday())
    return mon.isoformat(), (mon + timedelta(days=6)).isoformat()


def load_rows(ledger_path, start: str, end: str, market: str):
    p = Path(ledger_path)
    if not p.exists():
        return []
    out = []
    with open(p, newline="") as f:
        for r in csv.DictReader(f):
            if not (start <= (r.get("date") or "") <= end) or r.get("final_status") in SKIP:
                continue
            res, net = _f(r.get("result_pct")), _f(r.get("net_result_pct"))
            if res is None or net is None:
                continue
            strat = r.get("strategy") or ""
            if market == "NSE" and r.get("engine") == "live":
                strat = "live_scored"
            real = _f(r.get("realistic_net_pct"))
            if real is None:        # older rows: derive it the same way the ledger does
                import outcomes_ledger as _L
                real = _L.realistic_net_pct(res, _f(r.get("spread_pct")))
            out.append({"strat": strat, "date": r["date"], "res": res, "net": net, "real": real,
                        "t1": str(r.get("t1_hit")).lower() == "true", "key": r.get("setup_key") or r.get("trade_id"),
                        "sym": r.get("symbol"), "dir": r.get("direction"), "at": r.get("alert_time") or ""})
    return out


def score_rows(rows):
    by = {}
    for r in rows:
        by.setdefault(r["strat"], []).append(r)
    out = []
    for s, g in by.items():
        n = len(g)
        nets = [x["net"] for x in g]
        gw, gl = sum(v for v in nets if v > 0), -sum(v for v in nets if v < 0)
        pf = gw / gl if gl > 0 else (9.99 if gw > 0 else 0.0)
        exp = sum(nets) / n
        rexp = sum(x["real"] for x in g) / n
        win = sum(1 for x in g if x["res"] > 0) / n * 100
        days = {}
        for x in g:
            days.setdefault(x["date"], []).append(x["net"])
        cons = sum(1 for v in days.values() if sum(v) / len(v) > 0) / len(days) * 100
        t1 = sum(1 for x in g if x["t1"]) / n
        clip = lambda v: max(0.0, min(1.0, v))
        base = (40 * clip((exp + 10) / 20) + 20 * clip((min(pf, 9.99) - 0.5) / 1.5) + 15 * clip((win - 20) / 40)
                + 15 * clip(cons / 100) + 10 * clip(t1 / 0.6))
        score = base * min(1.0, math.sqrt(n / 30))
        out.append({"strat": s, "label": LABELS.get(s, s), "n": n, "setups": len({x["key"] for x in g}), "win": win,
                    "exp": exp, "rexp": rexp, "pf": min(pf, 9.99), "cons": cons, "score": score,
                    "sample": "thin" if n < 10 else ("building" if n < 30 else "solid"), "ev": evidence(g)})
    out.sort(key=lambda r: -r["score"])
    return out


# --- evidence quality (2026-11-12): how much of a strategy's result can be trusted -----------------------------------
def sample_label(n: int) -> str:
    return "Very thin" if n < 10 else "Building" if n < 30 else "Preliminary" if n < 100 else "Developing" if n < 200 else "Larger sample"


def wilson_ci(wins: int, n: int, z: float = 1.96):
    """95% Wilson interval for a win rate, in percent."""
    if n <= 0:
        return 0.0, 100.0
    p = wins / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half) * 100, min(1.0, centre + half) * 100


def _clusters(g):
    """Independent opportunities: trades on the same underlying, direction and day within 30 minutes are one market move
    (ITM/ATM/OTM variants of one signal are not independent evidence). Returns {cluster_id: mean net}, chronological."""
    order = sorted(g, key=lambda x: (x["date"], x["at"]))
    out, last = {}, {}
    for x in order:
        k = (x["date"], x["sym"], x["dir"])
        try:
            t = datetime.fromisoformat(x["at"])
        except ValueError:
            t = None
        prev = last.get(k)
        if prev is not None and t is not None and prev[1] is not None and (t - prev[1]).total_seconds() <= 1800:
            cid = prev[0]
        else:
            cid = (k, x["at"])
        last[k] = (cid, t if t is not None else None)
        out.setdefault(cid, []).append(x)
    return [(cid, sum(v["real"] for v in xs) / len(xs), xs[0]["date"]) for cid, xs in out.items()]


def bootstrap_ci(values, n_boot: int = 2000, seed: int = 7):
    """95% bootstrap interval of the mean (deterministic seed so a re-run gives the same report)."""
    import random
    if len(values) < 2:
        return None, None
    rnd = random.Random(seed)
    means = sorted(sum(rnd.choice(values) for _ in values) / len(values) for _ in range(n_boot))
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot) - 1]


def max_drawdown_and_streak(g):
    """Chronological drawdown of cumulative realistic net % (sum of trade results) and the longest losing streak."""
    peak = cum = dd = 0.0
    streak = best = 0
    for x in sorted(g, key=lambda x: (x["date"], x["at"])):
        cum += x["real"]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
        streak = streak + 1 if x["real"] <= 0 else 0
        best = max(best, streak)
    return dd, best


def evidence(g):
    """Everything the validation status rests on. All returns are realistic net % per trade (ask-in / bid-out + costs)."""
    n = len(g)
    wins = sum(1 for x in g if x["res"] > 0)
    cl = _clusters(g)
    cvals = [m for _, m, _ in cl]
    lo, hi = bootstrap_ci(cvals)
    dd, streak = max_drawdown_and_streak(g)
    sessions = len({x["date"] for x in g})
    ordered = sorted(g, key=lambda x: (x["date"], x["at"]))
    cut = int(len(ordered) * 0.7)
    oos = ordered[cut:]
    oos_exp = sum(x["real"] for x in oos) / len(oos) if len(oos) >= 10 else None       # out-of-sample only when it has substance
    w_lo, w_hi = wilson_ci(wins, n)
    rexp = sum(x["real"] for x in g) / n
    # Validation ladder (a weekly report can never reach Paper/Live validated).
    if n >= 100 and sessions >= 15 and lo is not None and lo > 0 and oos_exp is not None and oos_exp > 0:
        status = "Shadow validated"
    elif n >= 30 and sessions >= 5 and len(cl) >= 15 and rexp > 0:
        status = "Research candidate"
    elif rexp > 0:
        status = "Provisional leader" if n >= 5 else "Not enough data"
    else:
        status = "Not validated"
    return {"n": n, "clusters": len(cl), "sessions": sessions, "win_ci": (w_lo, w_hi), "real_exp": rexp,
            "boot": (lo, hi), "max_dd": dd, "max_streak": streak, "oos_exp": oos_exp, "status": status,
            "sample": sample_label(n)}


def _table(d, y, title, rows, note):
    f_h, f_b, f_s = _font(28, True), _font(21), _font(17)
    d.text((PAD, y), title, font=f_h, fill=NAVY)
    d.text((PAD + 420, y + 9), note, font=f_s, fill=MUTED)
    y += 50
    cols = [("#", 40, "l"), ("Strategy", 330, "l"), ("Trades", 100, "r"), ("Win", 100, "r"), ("Net/trade", 140, "r"), ("Realistic", 130, "r"),
            ("PF", 90, "r"), ("Win days", 120, "r"), ("Sample", 120, "r")]
    cx = PAD + 12
    for name, wd, al in cols:
        d.text((cx if al == "l" else cx + wd - 14 - d.textlength(name, font=f_s), y), name, font=f_s, fill=MUTED)
        cx += wd
    bx0, bx1 = cx + 20, W - PAD - 20
    d.text((bx0, y), "Score (out of 100)", font=f_s, fill=MUTED)
    y += 30
    if not rows:
        d.text((PAD + 12, y + 8), "No closed trades this week.", font=f_b, fill=MUTED)
        return y + 50
    for i, r in enumerate(rows):
        if i % 2:
            d.rectangle((PAD, y, W - PAD, y + 40), fill=(248, 249, 251))
        vals = [str(i + 1), r["label"], str(r["n"]), f"{r['win']:.0f}%", f"{r['exp']:+.2f}%", f"{r['rexp']:+.2f}%", f"{r['pf']:.2f}",
                f"{r['cons']:.0f}%", r["sample"]]
        cx = PAD + 12
        for (name, wd, al), v in zip(cols, vals):
            c = INK
            if name == "Net/trade":
                c = GREEN if r["exp"] >= 0 else RED
            if name == "Realistic":
                c = GREEN if r["rexp"] >= 0 else RED
            if name == "Sample" and v == "thin":
                c = AMBER
            d.text((cx if al == "l" else cx + wd - 14 - d.textlength(v, font=f_b), y + 8), v, font=f_b, fill=c)
            cx += wd
        w = (bx1 - bx0 - 70) * max(0.0, min(100.0, r["score"])) / 100
        d.rectangle((bx0, y + 10, bx0 + w, y + 30), fill=GREEN if i == 0 else (150, 160, 178))
        d.text((bx0 + w + 8, y + 8), f"{r['score']:.1f}", font=f_b, fill=INK)
        y += 40
    return y + 24


def _evidence_block(d, y, name, rows):
    """Evidence quality for the leaders: independent opportunities, session count, intervals, drawdown, validation status."""
    f_h, f_b, f_s = _font(24, True), _font(18), _font(16)
    top = [r for r in rows if r["n"] >= 5][:3]
    if not top:
        return y
    d.text((PAD, y + 6), f"{name} evidence quality (top {len(top)}, realistic net %)", font=f_h, fill=NAVY)
    y += 44
    for r in top:
        e = r["ev"]
        lo, hi = e["boot"]
        ci = f"{lo:+.1f} to {hi:+.1f}" if lo is not None else "n/a"
        oos = f"{e['oos_exp']:+.1f}%" if e["oos_exp"] is not None else "not enough"
        col = GREEN if e["status"] in ("Research candidate", "Shadow validated") else (AMBER if e["status"] == "Provisional leader" else RED)
        d.text((PAD + 12, y), f"{r['label']}", font=f_b, fill=INK)
        d.text((PAD + 330, y), e["status"], font=f_b, fill=col)
        y += 26
        d.text((PAD + 12, y), (f"{e['n']} trades = {e['clusters']} independent moves over {e['sessions']} sessions ({e['sample']})  |  "
                               f"win rate {e['win_ci'][0]:.0f}-{e['win_ci'][1]:.0f}% (95% CI)  |  expectancy {e['real_exp']:+.1f}% (95% CI {ci})"),
               font=f_s, fill=MUTED)
        y += 22
        d.text((PAD + 12, y), (f"peak-to-trough drop {e['max_dd']:.0f} pts (sum of trade %)  |  longest losing run {e['max_streak']}  |  "
                               f"out-of-sample (last 30%): {oos}"), font=f_s, fill=MUTED)
        y += 32
    return y + 10


def render(nse_rows, mcx_rows, start, end, path) -> bool:
    nse, mcx = score_rows(nse_rows), score_rows(mcx_rows)
    if not nse and not mcx:
        return False
    h = 280 + 90 + 60 * 2 + 40 * (max(len(nse), 1) + max(len(mcx), 1)) + 220 + 2 * (54 + 3 * 80)
    img = Image.new("RGB", (W, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 120), fill=NAVY)
    d.text((PAD, 22), "Weekly strategy scorecard", font=_font(46, True), fill=(255, 255, 255))
    d.text((PAD, 80), f"{start} to {(date.fromisoformat(start) + timedelta(days=4)).isoformat()}  |  shadow trades after assumed costs  |  data as of {datetime.now().strftime('%Y-%m-%d %H:%M')} IST  |  provisional",
           font=_font(21), fill=(200, 206, 220))
    y = 150
    for name, rows in (("NSE", nse), ("MCX", mcx)):
        top = rows[0] if rows else None
        txt = (f"{name} winner: {top['label']}  |  score {top['score']:.1f}  |  {top['n']} trades, "
               f"{top['win']:.0f}% win, {top['exp']:+.2f}% per trade") if top else f"{name}: no closed trades"
        bg = GREEN_BG if top and top["exp"] >= 0 else AMBER_BG
        d.rectangle((PAD, y, W - PAD, y + 62), fill=bg)
        d.text((PAD + 16, y + 16), txt, font=_font(26, True), fill=INK)
        y += 84
    y += 10
    y = _table(d, y, "NSE strategies", nse, "ranked by score")
    y = _table(d, y, "MCX strategies", mcx, "ranked by score; thin sample below 10 trades")
    y = _evidence_block(d, y, "NSE", nse)
    y = _evidence_block(d, y, "MCX", mcx)
    d.line((PAD, y, W - PAD, y), fill=LINE, width=2)
    notes = ["Realistic = same trades if bought at the ask and sold at the bid plus slippage (what a real fill would give).", "Score = net per trade 40 + profit factor 20 + win rate 15 + winning days 15 + first target 10, scaled down below 30 trades.",
             "Validation: a weekly report can reach at most Shadow validated (needs 100+ trades, 15+ sessions, positive CI and out-of-sample). Not a win probability.", "Shadow trades only (no real orders). Day direction drives much of the spread; judge after 2-3 weeks before retiring or promoting an engine."]
    for i, t in enumerate(notes):
        d.text((PAD, y + 14 + i * 28), t, font=_font(18), fill=MUTED)
    img.crop((0, 0, W, y + 130)).save(path)
    return True


def render_week(nse_ledger, mcx_ledger, any_date, path):
    start, end = week_bounds(any_date)
    ok = render(load_rows(nse_ledger, start, end, "NSE"), load_rows(mcx_ledger, start, end, "MCX"), start, end, path)
    return (start, (date.fromisoformat(start) + timedelta(days=4)).isoformat()) if ok else None
