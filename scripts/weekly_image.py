"""Render the weekly one-lot report (weekly_report.py) as a PNG for Telegram. Pillow only; same look as eod_image.py."""
from datetime import date

from PIL import Image, ImageDraw

import weekly_report
from eod_image import (NAVY, INK, MUTED, LINE, GREEN, RED, AMBER, GREY, GREEN_BG, RED_BG, W, PAD, _font, _money, _signed_col)

RH = 38


def _hbar(d, x0, x1, y, h, val, mx, half_fill=(GREEN, RED)):
    mid = (x0 + x1) / 2
    half = (x1 - x0) / 2 - 6
    w = abs(val) / max(mx, 1) * half
    d.line((mid, y - 4, mid, y + h + 4), fill=(160, 165, 175), width=1)
    if val >= 0:
        d.rectangle((mid, y, mid + w, y + h), fill=half_fill[0])
    else:
        d.rectangle((mid - w, y, mid, y + h), fill=half_fill[1])


def _table(d, y, heads, rows, f_small, f_body, bars=None, total=None):
    """heads: (name, width, align); rows: list of tuples of strings; bars: list of numbers (or None)."""
    cx = PAD + 12
    for name, wd, al in heads:
        tx = cx if al == "l" else cx + wd - 14 - d.textlength(name, font=f_small)
        d.text((tx, y), name, font=f_small, fill=MUTED)
        cx += wd
    bx0, bx1 = PAD + 12 + sum(h[1] for h in heads) + 30, W - PAD - 20
    if bars is not None:
        d.text((bx0, y), "P&L before costs", font=f_small, fill=MUTED)
    y += 24
    mx = max([abs(b) for b in (bars or [0])] + [1])
    for i, (row, b) in enumerate(zip(rows, bars or [None] * len(rows))):
        if i % 2:
            d.rectangle((PAD, y, W - PAD, y + RH), fill=(248, 249, 251))
        cx = PAD + 12
        for (name, wd, al), val in zip(heads, row):
            c = INK
            if name.endswith("Rs") and val not in ("-", ""):
                c = _signed_col(int(val.replace(",", "").replace("+", "")))
            tx = cx if al == "l" else cx + wd - 14 - d.textlength(val, font=f_body)
            d.text((tx, y + 9), val, font=f_body, fill=c)
            cx += wd
        if b is not None:
            _hbar(d, bx0, bx1, y + 9, RH - 18, b, mx)
        y += RH
    if total:
        d.line((PAD, y, W - PAD, y), fill=LINE, width=2)
        cx = PAD + 12
        f = _font(18, True)
        for (name, wd, al), val in zip(heads, total):
            c = _signed_col(int(val.replace(",", "").replace("+", ""))) if name.endswith("Rs") else INK
            tx = cx if al == "l" else cx + wd - 14 - d.textlength(val, font=f)
            d.text((tx, y + 9), val, font=f, fill=c)
            cx += wd
        y += RH
    return y


def render(data: dict, path: str) -> bool:
    s = weekly_report.summarize(data)
    a = s["all"]
    if not a["n"]:
        return False
    f_title, f_big, f_kpi, f_head = _font(34, True), _font(54, True), _font(34, True), _font(19, True)
    f_body, f_small = _font(18), _font(15)
    n_rows = len(s["days"]) + len(s["engines"]) + len(s["buckets"]) + 2
    height = 118 + 110 + 140 + 60 * 4 + RH * (n_rows + 4) + 24 * 4 + 200 + 150 + 120
    img = Image.new("RGB", (W, height), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 96), fill=NAVY)
    d.text((PAD, 14), "Weekly trade report", font=f_title, fill=(255, 255, 255))
    d.text((PAD, 58), f"{data['start']}  to  {(date.fromisoformat(data['start']) + __import__('datetime').timedelta(days=4)).isoformat()}   |   {len(s['days'])} trading day(s) so far   |   live alerts + shadow engines",
           font=f_body, fill=(176, 188, 214))
    badge = "ONE LOT PER TRADE"
    bw = d.textlength(badge, font=f_head) + 36
    d.rounded_rectangle((W - PAD - bw, 30, W - PAD, 68), 10, outline=(176, 188, 214), width=2)
    d.text((W - PAD - bw + 18, 38), badge, font=f_head, fill=(255, 255, 255))

    y = 118
    good = a["net"] >= 0
    d.rounded_rectangle((PAD, y, W - PAD, y + 116), 16, fill=GREEN_BG if good else RED_BG)
    col = GREEN if good else RED
    d.text((PAD + 28, y + 14), _money(a["net"]), font=f_big, fill=col)
    d.text((PAD + 30, y + 82), "week total after estimated costs", font=f_small, fill=MUTED)
    gx = W // 2 - 60
    d.text((gx, y + 22), "Before costs", font=f_small, fill=MUTED)
    d.text((gx, y + 44), _money(a["gross"]), font=f_kpi, fill=_signed_col(a["gross"]))
    d.text((gx + 300, y + 22), "T2 wins / closed", font=f_small, fill=MUTED)
    d.text((gx + 300, y + 44), f"{a['wins']} / {a['n']}", font=f_kpi, fill=INK)
    d.text((gx + 600, y + 22), "Win rate (T2)", font=f_small, fill=MUTED)
    d.text((gx + 600, y + 44), f"{a['wins'] / a['n'] * 100:.0f}%", font=f_kpi, fill=INK)
    y += 116 + 22

    def section(title, y):
        d.rectangle((0, y, W, y + 40), fill=NAVY)
        d.text((PAD, y + 8), title, font=f_head, fill=(255, 255, 255))
        return y + 40 + 10

    y = section("DAY BY DAY", y)
    wk = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    heads = (("Day", 250, "l"), ("Trades", 90, "r"), ("T2", 70, "r"), ("Losses", 90, "r"), ("Before costs Rs", 190, "r"),
             ("After costs Rs", 190, "r"))
    rows = [(f"{r['date']} ({wk[date.fromisoformat(r['date']).weekday()]})", str(r["n"]), str(r["wins"]), str(r["losses"]),
             f"{r['gross']:+,}", f"{r['net']:+,}") for r in s["days"]]
    y = _table(d, y, heads, rows, f_small, f_body, [r["gross"] for r in s["days"]],
               ("WEEK", str(a["n"]), str(a["wins"]), str(a["losses"]), f"{a['gross']:+,}", f"{a['net']:+,}")) + 22

    y = section("BY ENGINE / STRATEGY", y)
    heads_e = (("Engine", 250, "l"),) + heads[1:]
    rows = [(r["label"], str(r["n"]), str(r["wins"]), str(r["losses"]), f"{r['gross']:+,}", f"{r['net']:+,}") for r in s["engines"]]
    y = _table(d, y, heads_e, rows, f_small, f_body, [r["gross"] for r in s["engines"]]) + 22

    y = section("BY ENTRY TIME  |  INDEX vs STOCK", y)
    heads_t = (("Group", 250, "l"),) + heads[1:]
    rows = [(f"Entry {r['label']}", str(r["n"]), str(r["wins"]), str(r["losses"]), f"{r['gross']:+,}", f"{r['net']:+,}") for r in s["buckets"]]
    rows += [(nm, str(g["n"]), str(g["wins"]), str(g["losses"]), f"{g['gross']:+,}", f"{g['net']:+,}")
             for nm, g in (("Index options", s["index"]), ("Stock options", s["stock"])) if g["n"]]
    y = _table(d, y, heads_t, rows, f_small, f_body, [int(r[4].replace(",", "").replace("+", "")) for r in rows]) + 22

    if s["rules"]["Original SL"][1]:
        y = section(f"WHAT IF EVERY TRADE USED A DIFFERENT EXIT RULE?  ({s['rules']['Original SL'][1]} of {a['n']} trades, before costs)", y)
        rw = (W - 2 * PAD - 3 * 14) / 4
        for i, (name, (val, _k)) in enumerate(s["rules"].items()):
            x0 = PAD + i * (rw + 14)
            d.rounded_rectangle((x0, y, x0 + rw, y + 64), 12, fill=(247, 248, 251), outline=LINE, width=2)
            d.text((x0 + 16, y + 7), name, font=f_small, fill=MUTED)
            d.text((x0 + 16, y + 28), _money(val), font=_font(24, True), fill=_signed_col(val))
        y += 64 + 8
        if s.get("assumed"):
            d.text((PAD + 8, y), f"{s['assumed']} older trades lack pullback data: T2 hits assumed no pullback; T1-then-SL trades assumed to exit at entry.",
                   font=f_small, fill=MUTED)
        y += 24

    y = section("BEST AND WORST TRADES OF THE WEEK (one lot, before costs)", y)
    half = (W - 2 * PAD - 20) / 2
    for j, (title, lst, tcol) in enumerate((("Best", s["best"], GREEN), ("Worst", s["worst"], RED))):
        x0 = PAD + j * (half + 20)
        d.text((x0 + 8, y), title, font=f_head, fill=tcol)
        for i, t in enumerate(lst):
            yy = y + 30 + i * 34
            d.text((x0 + 8, yy), f"{t['symbol']} {t['dir']} {str(t['strike']).replace('.0', '')}  |  {t['label']}  |  {t['date'][5:]}", font=f_body, fill=INK)
            v = f"{t['gross']:+,.0f}"
            d.text((x0 + half - 14 - d.textlength(v, font=f_head), yy), v, font=f_head, fill=tcol)
    y += 30 + 4 * 34 + 16

    d.line((PAD, y + 4, W - PAD, y + 4), fill=LINE, width=2)
    d.text((PAD, y + 14), "P&L = entry x lot size x result %, before brokerage/taxes; after-cost totals use an assumed round-trip cost. "
           "Same contract and time under several engines counted once.", font=f_small, fill=MUTED)
    d.text((PAD, y + 36), "Trades still open at the end of a day (older shadow carry-overs) are not included. Shadow rows are data collection, "
           "not signals. Educational only; not a SEBI-registered advisor.", font=f_small, fill=MUTED)
    img.crop((0, 0, W, y + 66)).save(path)
    return True
