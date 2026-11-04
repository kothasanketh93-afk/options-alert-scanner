"""Render the end-of-day trade report (see eod_report.py) as a PNG table for Telegram. Pillow only."""
import os

from PIL import Image, ImageDraw, ImageFont

import eod_report

_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/dejavu/", "")
W, PAD = 1500, 28
COLS = (("Stock / option", 260, "l"), ("Engine", 200, "l"), ("Time", 70, "l"), ("Lot", 90, "r"), ("Entry", 100, "r"),
        ("SL", 100, "r"), ("T1", 100, "r"), ("T2", 100, "r"), ("Result", 170, "l"), ("Net %", 100, "r"),
        ("1-lot P&L Rs", 150, "r"))
STYLE = {  # group id -> (title, band fill, text colour)
    "win": ("TARGET 2 HIT", (214, 240, 222), (20, 110, 50)),
    "partial": ("T1 HIT, THEN STOPPED OUT (loss at original SL)", (253, 238, 200), (150, 95, 0)),
    "loss": ("STOP-LOSS HIT", (252, 222, 222), (170, 30, 30)),
    "other": ("STILL OPEN / NOT ENTERED", (230, 232, 236), (80, 85, 95)),
}


def _font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in _FONT_DIRS:
        try:
            return ImageFont.truetype(d + name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _num(x):
    return "-" if x is None else (f"{x:.2f}" if isinstance(x, (int, float)) else str(x))


def _row_cells(r):
    t = r["t"]
    strike = t.get("strike")
    strike = int(strike) if isinstance(strike, float) and strike == int(strike) else strike
    net = f"{r['net']:+.1f}%" if r["net"] is not None else "-"
    pnl = f"{r['pnl_gross']:+,}" if r.get("pnl_gross") is not None else "-"
    eng = r["engine"] + (" (= live)" if r.get("twin_of_live") else "")
    return [f"{t.get('symbol')} {t.get('direction')} {strike}", eng, (t.get("alert_time") or "")[11:16],
            f"{r['lot']:,}" if r.get("lot") else "-",
            _num(t.get("tracked_entry_price") or t.get("alert_premium")), _num(t.get("stop_loss")),
            _num(t.get("target_1")), _num(t.get("target_2")), str(r["status"]).replace("_", " "), net, pnl]


def render(state: dict, date_str: str, path: str, lot_sizes: dict = None) -> bool:
    rows = eod_report.collect(state, lot_sizes)
    if not rows:
        return False
    by = {g[0]: [r for r in rows if eod_report._group_of(r["status"]) == g[0]] for g in eod_report._GROUPS}
    f_title, f_head, f_body, f_small = _font(30, True), _font(18, True), _font(18), _font(15)
    RH = 38
    tot = eod_report.totals(rows)
    has_pnl = tot["all"]["n"] > 0
    height = (60 if has_pnl else 0) + 150 + sum((74 + RH * len(by[g])) for g in by if by[g]) + 90
    img = Image.new("RGB", (W, height), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((PAD, 24), f"End-of-day trade report  |  {date_str}", font=f_title, fill=(25, 30, 40))
    chips = [("Tracked", len(rows), (60, 70, 90)), ("T2 hit", len(by["win"]), STYLE["win"][2]),
             ("T1 then SL", len(by["partial"]), STYLE["partial"][2]), ("SL hit", len(by["loss"]), STYLE["loss"][2]),
             ("Open", len(by["other"]), STYLE["other"][2])]
    x = PAD
    for label, n, col in chips:
        txt = f"{label}: {n}"
        wd = d.textlength(txt, font=f_head) + 36
        d.rounded_rectangle((x, 78, x + wd, 116), 10, outline=col, width=2)
        d.text((x + 18, 86), txt, font=f_head, fill=col)
        x += wd + 14
    y = 140
    if has_pnl:
        a = tot["all"]
        good = a["net"] >= 0
        col = STYLE["win"][2] if good else STYLE["loss"][2]
        d.rounded_rectangle((PAD, y, W - PAD, y + 46), 10, fill=(240, 247, 242) if good else (252, 238, 238))
        d.text((PAD + 14, y + 12), f"One lot per closed trade ({a['n']} trades): gross Rs {a['gross']:+,}   |   after est. costs Rs {a['net']:+,}",
               font=f_head, fill=col)
        parts = [f"{lab}: {tot[lab]['gross']:+,}" for lab in tot if lab != "all" and tot[lab]["n"]]
        txt = "   ".join(parts)
        d.text((W - PAD - 14 - d.textlength(txt, font=f_small), y + 15), txt, font=f_small, fill=(90, 95, 105))
        y += 60
    for gid, _title, _m in eod_report._GROUPS:
        if not by[gid]:
            continue
        title, band, ink = STYLE[gid]
        d.rectangle((PAD, y, W - PAD, y + 34), fill=band)
        d.text((PAD + 12, y + 7), f"{title}  ({len(by[gid])})", font=f_head, fill=ink)
        y += 36
        cx = PAD + 12
        for name, wd, al in COLS:
            tx = cx if al == "l" else cx + wd - 28 - d.textlength(name, font=f_small)
            d.text((tx, y + 4), name, font=f_small, fill=(110, 115, 125))
            cx += wd
        y += 22
        for i, r in enumerate(by[gid]):
            if i % 2:
                d.rectangle((PAD, y, W - PAD, y + RH), fill=(247, 248, 250))
            cx = PAD + 12
            for (name, wd, al), val in zip(COLS, _row_cells(r)):
                col = ink if name in ("Result", "Net %", "1-lot P&L Rs") else (30, 35, 45)
                tx = cx if al == "l" else cx + wd - 28 - d.textlength(val, font=f_body)
                d.text((tx, y + 9), val, font=f_body, fill=col)
                cx += wd
            y += RH
        y += 12
    d.text((PAD, y + 8), "Net % and after-cost totals use an assumed round-trip cost. Gross P&L = (exit - entry) x lot, before brokerage/taxes. Shadow = data collection, not signals.",
           font=f_small, fill=(110, 115, 125))
    d.text((PAD, y + 30), "Educational only; not a SEBI-registered advisor.", font=f_small, fill=(110, 115, 125))
    img.crop((0, 0, W, y + 62)).save(path)
    return True
