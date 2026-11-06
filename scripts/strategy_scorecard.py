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
          "sess_sweep": "Session sweep"}
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
            out.append({"strat": strat, "date": r["date"], "res": res, "net": net,
                        "t1": str(r.get("t1_hit")).lower() == "true", "key": r.get("setup_key") or r.get("trade_id")})
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
                    "exp": exp, "pf": min(pf, 9.99), "cons": cons, "score": score,
                    "sample": "thin" if n < 10 else ("building" if n < 30 else "solid")})
    out.sort(key=lambda r: -r["score"])
    return out


def _table(d, y, title, rows, note):
    f_h, f_b, f_s = _font(28, True), _font(21), _font(17)
    d.text((PAD, y), title, font=f_h, fill=NAVY)
    d.text((PAD + 420, y + 9), note, font=f_s, fill=MUTED)
    y += 50
    cols = [("#", 40, "l"), ("Strategy", 360, "l"), ("Trades", 100, "r"), ("Win", 100, "r"), ("Net/trade", 140, "r"),
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
        vals = [str(i + 1), r["label"], str(r["n"]), f"{r['win']:.0f}%", f"{r['exp']:+.2f}%", f"{r['pf']:.2f}",
                f"{r['cons']:.0f}%", r["sample"]]
        cx = PAD + 12
        for (name, wd, al), v in zip(cols, vals):
            c = INK
            if name == "Net/trade":
                c = GREEN if r["exp"] >= 0 else RED
            if name == "Sample" and v == "thin":
                c = AMBER
            d.text((cx if al == "l" else cx + wd - 14 - d.textlength(v, font=f_b), y + 8), v, font=f_b, fill=c)
            cx += wd
        w = (bx1 - bx0 - 70) * max(0.0, min(100.0, r["score"])) / 100
        d.rectangle((bx0, y + 10, bx0 + w, y + 30), fill=GREEN if i == 0 else (150, 160, 178))
        d.text((bx0 + w + 8, y + 8), f"{r['score']:.1f}", font=f_b, fill=INK)
        y += 40
    return y + 24


def render(nse_rows, mcx_rows, start, end, path) -> bool:
    nse, mcx = score_rows(nse_rows), score_rows(mcx_rows)
    if not nse and not mcx:
        return False
    h = 280 + 90 + 60 * 2 + 40 * (max(len(nse), 1) + max(len(mcx), 1)) + 220
    img = Image.new("RGB", (W, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 120), fill=NAVY)
    d.text((PAD, 22), "Weekly strategy scorecard", font=_font(46, True), fill=(255, 255, 255))
    d.text((PAD, 80), f"{start} to {(date.fromisoformat(start) + timedelta(days=4)).isoformat()}  |  shadow trades after assumed costs  |  provisional, small samples",
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
    d.line((PAD, y, W - PAD, y), fill=LINE, width=2)
    notes = ["Score = net per trade 40 + profit factor 20 + win rate 15 + winning days 15 + first target 10, scaled down below 30 trades.",
             "Shadow trades only (no real orders). Day direction drives much of the spread; judge after 2-3 weeks before retiring or promoting an engine."]
    for i, t in enumerate(notes):
        d.text((PAD, y + 14 + i * 28), t, font=_font(18), fill=MUTED)
    img.crop((0, 0, W, y + 90)).save(path)
    return True


def render_week(nse_ledger, mcx_ledger, any_date, path):
    start, end = week_bounds(any_date)
    ok = render(load_rows(nse_ledger, start, end, "NSE"), load_rows(mcx_ledger, start, end, "MCX"), start, end, path)
    return (start, (date.fromisoformat(start) + timedelta(days=4)).isoformat()) if ok else None
