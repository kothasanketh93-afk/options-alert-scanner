"""Render the end-of-day trade report (see eod_report.py) as a PNG for Telegram. Pillow only.

Layout: header banner, KPI cards, trade tables grouped by outcome, then a closing 'ONE LOT RESULT' section
(big total, per-engine bars, exit-rule comparison) and footer notes.
"""
from PIL import Image, ImageDraw, ImageFont

import eod_report

_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/dejavu/", "")
W, PAD = 1500, 28
NAVY, INK, MUTED, LINE = (20, 30, 54), (30, 35, 45), (110, 115, 125), (226, 229, 235)
GREEN, RED, AMBER, GREY = (21, 128, 61), (190, 38, 38), (176, 108, 0), (88, 94, 106)
GREEN_BG, RED_BG, AMBER_BG, GREY_BG = (220, 243, 228), (253, 226, 226), (254, 240, 205), (232, 234, 238)
COLS = (("Stock / option", 250, "l"), ("Engine", 190, "l"), ("Time", 70, "l"), ("Lot", 90, "r"), ("Entry", 100, "r"),
        ("SL", 100, "r"), ("T1", 100, "r"), ("T2", 100, "r"), ("Result", 190, "l"), ("Net %", 100, "r"),
        ("1-lot P&L Rs", 150, "r"))
STYLE = {  # group id -> (title, band fill, text colour)
    "win": ("TARGET 2 HIT", GREEN_BG, GREEN),
    "partial": ("T1 HIT, THEN STOPPED OR CLOSED (a stop-out here is a loss at the original SL)", AMBER_BG, AMBER),
    "loss": ("STOP-LOSS HIT", RED_BG, RED),
    "other": ("STILL OPEN / CLOSED AT END OF DAY / NOT ENTERED", GREY_BG, GREY),
}
RH = 38


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


def _money(v):
    return f"{'+' if v > 0 else ('-' if v < 0 else '')}Rs {abs(v):,}"


def _signed_col(v):
    return GREEN if v > 0 else (RED if v < 0 else GREY)


def _row_cells(r):
    t = r["t"]
    strike = t.get("strike")
    strike = int(strike) if isinstance(strike, float) and strike == int(strike) else strike
    net = f"{r['net']:+.1f}%" if r["net"] is not None else "-"
    pnl = f"{r['pnl_gross']:+,}" if r.get("pnl_gross") is not None else "-"
    eng = r["engine"] + (" (dup)" if r.get("twin_of_live") else "")
    return [f"{t.get('symbol')} {t.get('direction')} {strike}", eng, (t.get("alert_time") or "")[11:16],
            f"{r['lot']:,}" if r.get("lot") else "-",
            _num(t.get("tracked_entry_price") or t.get("alert_premium")), _num(t.get("stop_loss")),
            _num(t.get("target_1")), _num(t.get("target_2")), str(r["status"]).replace("_", " "), net, pnl]


def render(state: dict, date_str: str, path: str, lot_sizes: dict = None, title: str = "End-of-day trade report",
           subtitle: str = "live alerts + shadow engines (data collection)", engines=None, split=None) -> bool:
    """split = (heading, keyfn): optional second one-lot table grouped by keyfn(row) (MCX: by commodity)."""
    rows = eod_report.collect(state, lot_sizes, engines)
    if not rows:
        return False
    by = {g[0]: [r for r in rows if eod_report._group_of(r["status"]) == g[0]] for g in eod_report._GROUPS}
    summ = eod_report.engine_summary(rows)
    tot = eod_report.totals(rows)["all"]
    rules = eod_report.exit_rule_totals(rows)
    has_pnl = tot["n"] > 0
    summ2 = eod_report.engine_summary(rows, split[1]) if split else []

    f_title, f_big, f_kpi, f_head = _font(34, True), _font(54, True), _font(34, True), _font(19, True)
    f_body, f_small = _font(18), _font(15)

    table_h = sum(36 + 22 + RH * len(by[g]) + 16 for g in by if by[g])
    summary_h = (60 + 130 + 34 + RH * (len(summ) + 1) + 24 + ((34 + RH * (len(summ2) + 1) + 24) if summ2 else 0) + (110 if rules else 0) + 20) if has_pnl else 0
    height = 100 + 120 + table_h + summary_h + 110
    img = Image.new("RGB", (W, height), (255, 255, 255))
    d = ImageDraw.Draw(img)

    # ---- header banner
    d.rectangle((0, 0, W, 96), fill=NAVY)
    d.text((PAD, 14), title, font=f_title, fill=(255, 255, 255))
    d.text((PAD, 58), f"{date_str}   |   {subtitle}", font=f_body, fill=(176, 188, 214))
    badge = "ONE LOT PER TRADE"
    bw = d.textlength(badge, font=f_head) + 36
    d.rounded_rectangle((W - PAD - bw, 30, W - PAD, 68), 10, outline=(176, 188, 214), width=2)
    d.text((W - PAD - bw + 18, 38), badge, font=f_head, fill=(255, 255, 255))

    # ---- KPI cards
    y = 118
    closed = len(eod_report._closed_rows(rows))
    cards = [("Tracked", str(len(rows)), NAVY), ("T2 hit", str(len(by["win"])), GREEN),
             ("T1 then SL", str(len(by["partial"])), AMBER), ("SL hit", str(len(by["loss"])), RED),
             ("Open / EOD", str(len(by["other"])), GREY)]
    gap = 16
    cw = (W - 2 * PAD - gap * (len(cards) - 1)) / len(cards)
    for i, (label, val, col) in enumerate(cards):
        x0 = PAD + i * (cw + gap)
        d.rounded_rectangle((x0, y, x0 + cw, y + 86), 14, fill=(247, 248, 251), outline=LINE, width=2)
        d.rectangle((x0 + 2, y + 16, x0 + 8, y + 70), fill=col)
        d.text((x0 + 26, y + 8), val, font=f_kpi, fill=col)
        d.text((x0 + 26, y + 54), label, font=f_small, fill=MUTED)
    y += 86 + 24

    # ---- trade tables
    for gid, _t, _m in eod_report._GROUPS:
        if not by[gid]:
            continue
        title, band, ink = STYLE[gid]
        d.rounded_rectangle((PAD, y, W - PAD, y + 34), 8, fill=band)
        d.text((PAD + 14, y + 7), f"{title}  ({len(by[gid])})", font=f_head, fill=ink)
        y += 36
        cx = PAD + 12
        for name, wd, al in COLS:
            tx = cx if al == "l" else cx + wd - 28 - d.textlength(name, font=f_small)
            d.text((tx, y + 4), name, font=f_small, fill=MUTED)
            cx += wd
        y += 22
        for i, r in enumerate(by[gid]):
            if i % 2:
                d.rectangle((PAD, y, W - PAD, y + RH), fill=(248, 249, 251))
            cx = PAD + 12
            cells = _row_cells(r)
            for (name, wd, al), val in zip(COLS, cells):
                col = INK
                if name in ("Result",):
                    col = ink
                elif name in ("Net %", "1-lot P&L Rs") and val != "-":
                    col = GREEN if val.startswith("+") else RED
                f = _font(18, True) if name in ("Result", "1-lot P&L Rs") else f_body
                tx = cx if al == "l" else cx + wd - 28 - d.textlength(val, font=f)
                d.text((tx, y + 9), val, font=f, fill=col)
                cx += wd
            y += RH
        y += 16

    # ---- closing section: ONE LOT RESULT
    if has_pnl:
        y += 6
        d.rectangle((0, y, W, y + 46), fill=NAVY)
        d.text((PAD, y + 10), "ONE LOT RESULT", font=f_head, fill=(255, 255, 255))
        sub = f"{tot['n']} closed trades, one lot each; same contract + time under several engines counted once"
        d.text((W - PAD - d.textlength(sub, font=f_small), y + 15), sub, font=f_small, fill=(176, 188, 214))
        y += 46 + 14
        good = tot["net"] >= 0
        d.rounded_rectangle((PAD, y, W - PAD, y + 116), 16, fill=GREEN_BG if good else RED_BG)
        col = GREEN if good else RED
        d.text((PAD + 28, y + 14), _money(tot["net"]), font=f_big, fill=col)
        d.text((PAD + 30, y + 82), "after estimated costs", font=f_small, fill=MUTED)
        gx = W // 2 + 40
        d.text((gx, y + 22), "Before costs", font=f_small, fill=MUTED)
        d.text((gx, y + 44), _money(tot["gross"]), font=f_kpi, fill=_signed_col(tot["gross"]))
        wins = sum(s["wins"] for s in summ)
        d.text((gx + 330, y + 22), "T2 wins / closed", font=f_small, fill=MUTED)
        d.text((gx + 330, y + 44), f"{wins} / {tot['n']}", font=f_kpi, fill=INK)
        y += 116 + 18

        # per-engine table with P&L bars
        for first, items in ((("Engine", summ),) + (((split[0], summ2),) if summ2 else ())):
            heads = ((first, 250, "l"), ("Trades", 90, "r"), ("T2", 70, "r"), ("Losses", 90, "r"), ("Before costs Rs", 190, "r"),
                     ("After costs Rs", 190, "r"))
            cx = PAD + 12
            for name, wd, al in heads:
                tx = cx if al == "l" else cx + wd - 14 - d.textlength(name, font=f_small)
                d.text((tx, y), name, font=f_small, fill=MUTED)
                cx += wd
            bar_x0, bar_x1 = PAD + 12 + sum(h[1] for h in heads) + 40, W - PAD - 20
            d.text((bar_x0, y), "P&L before costs", font=f_small, fill=MUTED)
            y += 24
            mx = max([abs(s["gross"]) for s in items] + [1])
            mid = (bar_x0 + bar_x1) / 2
            half = (bar_x1 - bar_x0) / 2 - 6
            for i, s in enumerate(list(items) + [{"engine": "TOTAL", "n": tot["n"], "wins": wins,
                                                  "losses": sum(x["losses"] for x in items), "gross": tot["gross"], "net": tot["net"]}]):
                is_tot = s["engine"] == "TOTAL"
                if is_tot:
                    d.line((PAD, y, W - PAD, y), fill=LINE, width=2)
                elif i % 2:
                    d.rectangle((PAD, y, W - PAD, y + RH), fill=(248, 249, 251))
                f = _font(18, True) if is_tot else f_body
                vals = (s["engine"], f"{s['n']}", f"{s['wins']}", f"{s['losses']}", f"{s['gross']:+,}", f"{s['net']:+,}")
                cx = PAD + 12
                for (name, wd, al), val in zip(heads, vals):
                    c = INK
                    if name.endswith("Rs"):
                        c = _signed_col(int(val.replace(",", "").replace("+", "")))
                    tx = cx if al == "l" else cx + wd - 14 - d.textlength(val, font=f)
                    d.text((tx, y + 9), val, font=f, fill=c)
                    cx += wd
                if not is_tot:
                    bw2 = abs(s["gross"]) / mx * half
                    if s["gross"] >= 0:
                        d.rectangle((mid, y + 9, mid + bw2, y + RH - 9), fill=GREEN)
                    else:
                        d.rectangle((mid - bw2, y + 9, mid, y + RH - 9), fill=RED)
                    d.line((mid, y + 4, mid, y + RH - 4), fill=(160, 165, 175), width=1)
                y += RH
            y += 20

        if rules:
            d.text((PAD + 12, y), "What if every closed trade used a different exit rule? (one lot, before costs)", font=f_head, fill=INK)
            y += 34
            rw = (W - 2 * PAD - 3 * 14) / 4
            for i, (name, (val, known)) in enumerate(rules.items()):
                x0 = PAD + i * (rw + 14)
                d.rounded_rectangle((x0, y, x0 + rw, y + 62), 12, fill=(247, 248, 251), outline=LINE, width=2)
                d.text((x0 + 16, y + 7), name, font=f_small, fill=MUTED)
                d.text((x0 + 16, y + 28), _money(val), font=_font(24, True), fill=_signed_col(val))
            y += 62 + 16

    # ---- footer
    d.line((PAD, y + 4, W - PAD, y + 4), fill=LINE, width=2)
    d.text((PAD, y + 14), "Gross P&L = (exit - entry) x lot size, before brokerage/taxes; after-cost figures use an assumed round-trip cost. "
           "(dup) = same contract and time as an earlier row, counted once.", font=f_small, fill=MUTED)
    d.text((PAD, y + 36), "Shadow rows are data collection, not signals. Educational only; not a SEBI-registered advisor.",
           font=f_small, fill=MUTED)
    img.crop((0, 0, W, y + 66)).save(path)
    return True
