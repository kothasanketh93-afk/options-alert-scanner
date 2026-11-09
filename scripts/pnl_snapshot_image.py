"""Render the intraday P&L snapshot (pnl_snapshot.py) as a PNG for Telegram. Pillow only; same look as eod_image.py."""
from PIL import Image, ImageDraw

from eod_image import NAVY, INK, MUTED, LINE, GREEN, RED, GREY, W, PAD, _font, _signed_col

RH = 38


def _rs(v):
    return f"{'+' if v > 0 else ('-' if v < 0 else '')}Rs {abs(v):,.0f}"


def render(title: str, date_str: str, hhmm: str, summary: dict, open_rows: list, path: str, group_head: str = "Group") -> bool:
    tot = summary["ALL"]
    if not (tot["closed_n"] or tot["open_n"]):
        return False
    groups = sorted(((k, v) for k, v in summary.items() if k != "ALL"), key=lambda kv: -(kv[1]["net"] + kv[1]["mtm"]))
    opens = sorted(open_rows, key=lambda r: -abs(r["mtm"]))[:14]
    f_title, f_kpi, f_head, f_body, f_small = _font(34, True), _font(34, True), _font(19, True), _font(18), _font(15)
    height = 96 + 24 + 110 + 20 + 60 + RH * (len(groups) + 1) + 30 + (60 + RH * len(opens) + 20 if opens else 0) + 70
    img = Image.new("RGB", (W, height), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, 96), fill=NAVY)
    d.text((PAD, 14), f"P&L snapshot - {title}", font=f_title, fill=(255, 255, 255))
    d.text((PAD, 58), f"{date_str}   {hhmm[:2]}:{hhmm[2:]} IST   |   shadow data: not real orders", font=f_body, fill=(176, 188, 214))
    badge = "ONE LOT PER TRADE"
    bw = d.textlength(badge, font=f_head) + 36
    d.rounded_rectangle((W - PAD - bw, 30, W - PAD, 68), 10, outline=(176, 188, 214), width=2)
    d.text((W - PAD - bw + 18, 38), badge, font=f_head, fill=(255, 255, 255))

    y = 120
    total = tot["net"] + tot["mtm"]
    cards = [("Total now", _rs(total), _signed_col(total)), ("Closed net", _rs(tot["net"]), _signed_col(tot["net"])),
             ("Open mark-to-market", _rs(tot["mtm"]), _signed_col(tot["mtm"])),
             ("Closed trades", f"{tot['closed_n']}  ({tot['wins']} wins)", NAVY), ("Open trades", str(tot["open_n"]), GREY)]
    gap = 16
    cw = (W - 2 * PAD - gap * (len(cards) - 1)) / len(cards)
    for i, (label, val, col) in enumerate(cards):
        x0 = PAD + i * (cw + gap)
        d.rounded_rectangle((x0, y, x0 + cw, y + 96), 14, fill=(247, 248, 251), outline=LINE, width=2)
        d.rectangle((x0 + 2, y + 16, x0 + 8, y + 80), fill=col)
        d.text((x0 + 26, y + 12), val, font=f_kpi, fill=col)
        d.text((x0 + 26, y + 62), label, font=f_small, fill=MUTED)
    y += 96 + 28

    d.text((PAD, y), f"By {group_head.lower()}", font=f_head, fill=NAVY)
    y += 36
    heads = [(group_head, 380, "l"), ("Closed", 110, "r"), ("Wins", 100, "r"), ("Closed net", 200, "r"),
             ("Open", 100, "r"), ("Open MTM", 200, "r"), ("Total", 200, "r")]
    cx = PAD + 12
    for name, wd, al in heads:
        d.text((cx if al == "l" else cx + wd - 14 - d.textlength(name, font=f_small), y), name, font=f_small, fill=MUTED)
        cx += wd
    y += 24
    for i, (name, g) in enumerate(groups):
        if i % 2:
            d.rectangle((PAD, y, W - PAD, y + RH), fill=(248, 249, 251))
        t = g["net"] + g["mtm"]
        vals = [name, str(g["closed_n"]), str(g["wins"]), _rs(g["net"]), str(g["open_n"]), _rs(g["mtm"]), _rs(t)]
        cx = PAD + 12
        for (hn, wd, al), v in zip(heads, vals):
            c = INK
            if hn in ("Closed net", "Open MTM", "Total"):
                c = _signed_col(int(v.replace("Rs ", "").replace(",", "").replace("+", "").replace("-", "-") or 0)) if v != "Rs 0" else GREY
            d.text((cx if al == "l" else cx + wd - 14 - d.textlength(v, font=f_body), y + 9), v, font=f_body, fill=c)
            cx += wd
        y += RH
    y += 30
    if opens:
        d.text((PAD, y), "Open positions (largest moves)", font=f_head, fill=NAVY)
        y += 36
        oh = [("Instrument", 360, "l"), ("Strategy", 260, "l"), ("Entry", 120, "r"), ("Last", 120, "r"), ("Move", 120, "r"), ("MTM", 180, "r")]
        cx = PAD + 12
        for name, wd, al in oh:
            d.text((cx if al == "l" else cx + wd - 14 - d.textlength(name, font=f_small), y), name, font=f_small, fill=MUTED)
            cx += wd
        y += 24
        for i, r in enumerate(opens):
            if i % 2:
                d.rectangle((PAD, y, W - PAD, y + RH), fill=(248, 249, 251))
            vals = [r["name"], r["strategy"], f"{r['entry']:.2f}", f"{r['last']:.2f}", f"{r['pct']:+.1f}%", _rs(r["mtm"])]
            cx = PAD + 12
            for (hn, wd, al), v in zip(oh, vals):
                c = _signed_col(r["mtm"]) if hn in ("Move", "MTM") else INK
                d.text((cx if al == "l" else cx + wd - 14 - d.textlength(v, font=f_body), y + 9), v, font=f_body, fill=c)
                cx += wd
            y += RH
        y += 20
    d.line((PAD, y, W - PAD, y), fill=LINE, width=2)
    d.text((PAD, y + 14), "Closed net = after the assumed round-trip cost. Open MTM = last traded 1-minute close, before costs. "
           "Educational data, not advice.", font=f_small, fill=MUTED)
    img.crop((0, 0, W, y + 50)).save(path)
    return True
