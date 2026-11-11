"""Trade charts for Telegram (added 2026-11-11, for learning: "show me this kind of view for each trade").

Two pictures per notified trade, drawn with Pillow only, in the dark trading-app style:

* Setup chart (when the alert goes out): the UNDERLYING's 5-minute candles for the day with the levels behind the setup
  (signal price, previous-day high/low, the swept level, the fair value gap zone), auto support/resistance from swing
  points (orange, like a charting app) and candlestick patterns labelled where they formed. Bottom panel: the option,
  its premium entry/SL/T1/T2 and why the setup fired.
* Result chart (when the trade closes: SL / T2 / end of day): the OPTION premium's 1-minute candles from the alert to the
  exit with the entry, SL, T1 and T2 lines and the entry/exit points marked.

Pure drawing + small helpers; every caller wraps it so a chart problem can never block an alert. Educational only.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw

import candlestick_patterns
from eod_image import _font

IST = timezone(timedelta(hours=5, minutes=30))
W, CH, INFO_H = 1400, 720, 200
L, R, T, B = 20, 140, 96, 44                # plot margins inside the chart area (R holds the price axis)
BG, GRID, AXIS, TXT, MUTE = (19, 23, 34), (38, 44, 58), (120, 128, 145), (232, 235, 241), (150, 158, 175)
UP, DN = (38, 166, 154), (239, 83, 80)
ORANGE, BLUE, GREEN, RED, PURPLE, YELLOW = (255, 152, 0), (66, 133, 244), (46, 204, 113), (239, 83, 80), (171, 71, 188), (241, 196, 15)
CHART_ENABLED = True


def _ts(x):
    try:
        dt = datetime.fromisoformat(str(x))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


# --- analysis helpers ---------------------------------------------------------------------------------------------
def swing_levels(candles: list, k: int = 2, max_levels: int = 4) -> list:
    """Support/resistance from swing highs/lows (k candles each side), nearby swings merged; strongest (most touches) first.
    Returns [(price, touches)]."""
    if len(candles) < 2 * k + 3:
        return []
    rng = sum(c["high"] - c["low"] for c in candles) / len(candles)
    tol = max(rng * 0.6, candles[-1]["close"] * 0.0004)
    pts = []
    for i in range(k, len(candles) - k):
        win = candles[i - k:i + k + 1]
        if candles[i]["high"] >= max(c["high"] for c in win):
            pts.append(candles[i]["high"])
        if candles[i]["low"] <= min(c["low"] for c in win):
            pts.append(candles[i]["low"])
    clusters = []
    for p in sorted(pts):
        if clusters and p - clusters[-1][-1] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    lv = [(round(sum(c) / len(c), 2), len(c)) for c in clusters]
    return sorted(lv, key=lambda x: -x[1])[:max_levels]


def pattern_marks(candles: list, max_marks: int = 5, min_strength: float = 0.8) -> list:
    """Directional candlestick patterns where they completed: [(index, name, bias)], strongest, at least 3 candles apart."""
    found = []
    for i in range(2, len(candles)):
        try:
            det = [d for d in candlestick_patterns.detect(candles[:i + 1])
                   if d["bias"] in ("bullish", "bearish") and d["strength"] >= min_strength]
        except Exception:
            continue
        if det:
            d = max(det, key=lambda x: x["strength"])
            found.append((d["strength"], i, d["pattern"], d["bias"]))
    out = []
    for _s, i, name, bias in sorted(found, key=lambda x: (-x[0], -x[1])):
        if all(abs(i - j) >= 3 for j, _n, _b in out):
            out.append((i, name, bias))
        if len(out) >= max_marks:
            break
    return sorted(out)


# --- drawing ------------------------------------------------------------------------------------------------------
def _fmt(v, dec):
    return f"{v:,.{dec}f}"


def render(candles: list, path: str, title: str, subtitle: str = "", levels=(), zones=(), vlines=(), points=(),
           info=(), auto_levels: bool = True, patterns: bool = True, dec: int = 2) -> bool:
    """levels: (price, label, colour, dashed); zones: (low, high, label, colour); vlines: (timestamp, label, colour);
    points: (timestamp, price, label, colour); info: lines (str or (str, colour)) for the bottom panel."""
    cs = [c for c in candles or [] if _ts(c.get("timestamp")) and None not in (c.get("open"), c.get("high"), c.get("low"), c.get("close"))]
    cs.sort(key=lambda c: _ts(c["timestamp"]))
    if len(cs) < 3:
        return False
    info = list(info)
    H = CH + (24 + 28 * len(info) + 16 if info else 0)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_t, f_s, f_b, f_l = _font(30, True), _font(17), _font(16, True), _font(15)

    d.text((L + 6, 14), title, font=f_t, fill=TXT)
    d.text((L + 6, 56), subtitle, font=f_s, fill=MUTE)

    lo = min(c["low"] for c in cs)
    hi = max(c["high"] for c in cs)
    span = hi - lo or max(hi * 0.01, 1)
    # trade levels that are near the candles widen the scale; far ones are listed at the edge instead of squashing the chart
    keep = lambda p: p is not None and lo - 1.5 * span <= p <= hi + 1.5 * span
    extra = [p for p, *_ in levels if keep(p)] + [z for zz in zones for z in zz[:2] if keep(z)]
    lo, hi = min([lo] + extra), max([hi] + extra)
    pad = (hi - lo) * 0.06 or 1
    lo, hi = lo - pad, hi + pad
    x0, x1, y0, y1 = L, W - R, T, CH - B
    n = len(cs)
    step = (x1 - x0) / n
    X = lambda i: x0 + step * (i + 0.5)
    Y = lambda p: y1 - (p - lo) / (hi - lo) * (y1 - y0)
    t_index = {}
    for i, c in enumerate(cs):
        t_index[_ts(c["timestamp"])] = i

    def x_of(ts):
        t = _ts(ts)
        if t is None:
            return None
        best = min(range(n), key=lambda i: abs((_ts(cs[i]["timestamp"]) - t).total_seconds()))
        return X(best)

    # grid + price axis
    for g in range(6):
        p = lo + (hi - lo) * g / 5
        y = Y(p)
        d.line((x0, y, x1, y), fill=GRID)
        d.text((x1 + 10, y - 9), _fmt(p, dec), font=f_l, fill=AXIS)
    every = max(1, n // 8)
    for i in range(0, n, every):
        d.line((X(i), y0, X(i), y1), fill=GRID)
        d.text((max(X(i) - 20, L), y1 + 10), cs[i]["timestamp"][11:16], font=f_l, fill=AXIS)

    tags, names = [], []          # drawn last, nudged apart so nearby levels stay readable

    def tag(y, text, col):
        tags.append([y, text, col])

    def _spread(items, gap, limit=None):
        items.sort(key=lambda it: it[0])
        for k in range(1, len(items)):
            if items[k][0] - items[k - 1][0] < gap:
                items[k][0] = items[k - 1][0] + gap
        if items and limit is not None and items[-1][0] > limit:     # pushed past the bottom: slide the crowded run up
            shift = items[-1][0] - limit
            for k in range(len(items) - 1, -1, -1):
                items[k][0] -= shift
                if k and items[k][0] - items[k - 1][0] >= gap:
                    break
        return items

    def hline(p, col, dashed=False, width=2):
        y = Y(p)
        if dashed:
            x = x0
            while x < x1:
                d.line((x, y, min(x + 10, x1), y), fill=col, width=width)
                x += 18
        else:
            d.line((x0, y, x1, y), fill=col, width=width)

    # zones (e.g. fair value gap)
    for zlo, zhi, label, col in zones:
        if keep(zlo) and keep(zhi):
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(ov).rectangle((x0, Y(zhi), x1, Y(zlo)), fill=col + (55,))
            img.paste(Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB"))
            d = ImageDraw.Draw(img)
            d.text((x0 + 8, Y(zhi) + 3), label, font=f_l, fill=col)

    # de-duplicate setup levels at the same price (e.g. a swept previous-day low is also the setup level)
    tol = (hi - lo) * 0.004
    uniq = []
    for lv in levels:
        if lv[0] is not None and not any(u[0] is not None and abs(u[0] - lv[0]) <= tol for u in uniq):
            uniq.append(lv)
    levels = uniq

    # auto support / resistance (skipped where a setup level already sits)
    if auto_levels:
        for p, touches in swing_levels(cs):
            if any(abs(p - lv[0]) <= 3 * tol for lv in levels):
                continue
            hline(p, ORANGE, width=1)
            tag(Y(p), _fmt(p, dec), ORANGE)

    # candles
    bw = max(1.0, step * 0.62)
    for i, c in enumerate(cs):
        col = UP if c["close"] >= c["open"] else DN
        x = X(i)
        d.line((x, Y(c["high"]), x, Y(c["low"])), fill=col, width=1)
        top, bot = Y(max(c["open"], c["close"])), Y(min(c["open"], c["close"]))
        d.rectangle((x - bw / 2, top, x + bw / 2, max(bot, top + 1)), fill=col)

    # candlestick patterns
    if patterns:
        for i, name, bias in pattern_marks(cs):
            c = cs[i]
            col = UP if bias == "bullish" else DN
            tw = d.textlength(name, font=f_l) + 12
            if bias == "bullish":
                y = Y(c["low"]) + 10
                d.polygon(((X(i), y - 6), (X(i) - 6, y + 2), (X(i) + 6, y + 2)), fill=col)
                bx = min(max(X(i) - tw / 2, x0), x1 - tw)
                d.rounded_rectangle((bx, y + 2, bx + tw, y + 24), 5, fill=col)
                d.text((bx + 6, y + 4), name, font=f_l, fill=(255, 255, 255))
            else:
                y = Y(c["high"]) - 10
                d.polygon(((X(i), y + 6), (X(i) - 6, y - 2), (X(i) + 6, y - 2)), fill=col)
                bx = min(max(X(i) - tw / 2, x0), x1 - tw)
                d.rounded_rectangle((bx, y - 24, bx + tw, y - 2), 5, fill=col)
                d.text((bx + 6, y - 22), name, font=f_l, fill=(255, 255, 255))

    # trade / setup levels
    off = []
    for p, label, col, dashed in levels:
        if p is None:
            continue
        if not keep(p):
            off.append(f"{label} {_fmt(p, dec)}")
            continue
        hline(p, col, dashed)
        names.append([Y(p) - 22, label, col])
        tag(Y(p), _fmt(p, dec), col)
    if off:
        d.text((x0 + 8, y0 - 26), "Off-chart: " + " | ".join(off), font=f_l, fill=MUTE)

    for y, text, col in _spread(names, 20, y1 - 20):
        tw = d.textlength(text, font=f_b)
        d.rectangle((x1 - tw - 14, y - 1, x1 - 4, y + 19), fill=BG)
        d.text((x1 - tw - 8, y), text, font=f_b, fill=col)
    for y, text, col in _spread(tags, 24, y1):
        tw = d.textlength(text, font=f_l) + 12
        d.rectangle((x1 + 4, y - 11, x1 + 8 + tw, y + 11), fill=col)
        d.text((x1 + 10, y - 9), text, font=f_l, fill=(0, 0, 0) if col in (ORANGE, YELLOW, GREEN) else (255, 255, 255))

    # vertical markers and points
    for ts, label, col in vlines:
        x = x_of(ts)
        if x is None:
            continue
        y = y0
        while y < y1:
            d.line((x, y, x, min(y + 8, y1)), fill=col, width=2)
            y += 14
        d.text((min(x + 6, x1 - d.textlength(label, font=f_b) - 4), y0 + 4), label, font=f_b, fill=col)
    for ts, price, label, col in points:
        x = x_of(ts)
        if x is None or price is None:
            continue
        y = Y(price)
        d.ellipse((x - 8, y - 8, x + 8, y + 8), outline=col, width=3)
        tw = d.textlength(label, font=f_b)
        lx = x - 12 - tw if x > (x0 + x1) / 2 else x + 12
        d.rectangle((lx - 3, y + 10, lx + tw + 3, y + 30), fill=BG)
        d.text((lx, y + 11), label, font=f_b, fill=col)

    # info panel
    if info:
        y = CH + 8
        d.line((L, y, W - L, y), fill=GRID, width=2)
        y += 16
        for line in info:
            text, col = (line, TXT) if isinstance(line, str) else line
            d.text((L + 6, y), text, font=f_s, fill=col)
            y += 28
    img.save(path)
    return True


# --- trade-specific builders --------------------------------------------------------------------------------------
_LEVEL_NAMES = {"PDH": "Previous-day high", "PDL": "Previous-day low", "ACC_LOW": "Range low", "ACC_HIGH": "Range high",
                "OI_FLOW": "OI flow spot", "FVG": "Gap edge", "BOS": "Structure level", "GAP_OR_HIGH": "Opening-range high",
                "GAP_OR_LOW": "Opening-range low"}


def _num(x):
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def setup_chart(trade: dict, candles: list, path: str, name: str = None, strategy: str = None, pdh=None, pdl=None,
                lot=None, dec: int = 2) -> bool:
    """Underlying candles with the setup's levels + an info panel about the option trade."""
    sym = name or trade.get("symbol")
    strike = trade.get("strike")
    strike = f"{strike:g}" if isinstance(strike, (int, float)) else strike
    bull = trade.get("direction") == "CE"
    levels = [(_num(trade.get("spot_price")), "Signal price", BLUE, False),
              (_num(pdh), "Previous-day high", PURPLE, True), (_num(pdl), "Previous-day low", PURPLE, True)]
    lv_name, lv_val = trade.get("level_name"), _num(trade.get("level_value"))
    if lv_val is not None:
        levels.append((lv_val, _LEVEL_NAMES.get(lv_name, lv_name or "Level"), YELLOW, True))
    if _num(trade.get("sweep_extreme")) is not None:
        levels.append((_num(trade["sweep_extreme"]), "Invalidation (idea fails beyond)", RED, True))
    zones = []
    fvg = trade.get("fvg")
    if isinstance(fvg, (list, tuple)) and len(fvg) == 2 and None not in fvg:
        zones.append((float(fvg[0]), float(fvg[1]), "Fair value gap", PURPLE))
    e, sl, t1, t2 = (_num(trade.get(k)) for k in ("alert_premium", "stop_loss", "target_1", "target_2"))
    one_lot = ""
    if lot and e:
        one_lot = (f"   |   one lot: risk Rs {abs((sl or e) - e) * lot:,.0f}, T1 +Rs {((t1 or e) - e) * lot:,.0f}, "
                   f"T2 +Rs {((t2 or e) - e) * lot:,.0f}")
    why = "; ".join((trade.get("why_reasons") or [])[:2])
    info = [(f"Option: BUY {sym} {strike} {trade.get('direction')}   ({'bullish' if bull else 'bearish'} view; "
             f"a seller would SELL the {strike} {'PE' if bull else 'CE'})", TXT),
            (f"Premium  entry {e}  |  SL {sl}  |  T1 {t1}  |  T2 {t2}{one_lot}", YELLOW)]
    if why:
        info.append((f"Why: {why}"[:150], MUTE))
    info.append(("Orange = auto support/resistance from swing points. Labels = candlestick patterns. "
                 "Shadow data, not advice.", MUTE))
    return render(candles, path, f"{sym}  {'BULLISH' if bull else 'BEARISH'} setup  ->  {strike} {trade.get('direction')}",
                  f"{strategy or trade.get('strategy') or ''}   |   underlying 5-minute chart   |   signal at "
                  f"{str(trade.get('alert_time') or '')[11:16]}",
                  levels=levels, zones=zones, vlines=[(trade.get("alert_time"), "Signal", BLUE)], info=info, dec=dec)


def outcome_chart(trade: dict, candles: list, path: str, name: str = None, strategy: str = None, lot=None) -> bool:
    """Option premium candles from the alert to the exit with entry/SL/T1/T2 and the entry/exit points."""
    import sell_view
    sym = name or trade.get("symbol")
    strike = trade.get("strike")
    strike = f"{strike:g}" if isinstance(strike, (int, float)) else strike
    status = trade.get("final_status") or trade.get("status")
    t_out = sell_view.exit_time(trade)
    t_in = _ts(trade.get("alert_time"))
    end = _ts(t_out)
    cs = [c for c in candles or [] if _ts(c.get("timestamp")) and (t_in is None or _ts(c["timestamp"]) >= t_in - timedelta(minutes=10))
          and (end is None or _ts(c["timestamp"]) <= end + timedelta(minutes=10))]
    entry = _num(trade.get("tracked_entry_price"))
    sl0 = _num(trade.get("initial_stop_loss")) or _num(trade.get("stop_loss"))
    levels = [(entry, "Entry", BLUE, False), (sl0, "Stop-loss", RED, True),
              (_num(trade.get("target_1")), "Target 1", GREEN, True), (_num(trade.get("target_2")), "Target 2", GREEN, False)]
    if _num(trade.get("stop_loss")) not in (None, sl0):
        levels.append((_num(trade["stop_loss"]), "Moved stop", ORANGE, True))
    import outcomes_ledger
    exit_px = {"T2_HIT": trade.get("target_2"), "EOD_CLOSED": trade.get("eod_last_price"),
               "T1_HIT_THEN_EOD": trade.get("eod_last_price")}.get(status, trade.get("stop_loss"))
    res = outcomes_ledger.result_pct(trade)
    pnl = f"   |   one lot Rs {(exit_px - entry) * lot:+,.0f} before costs" if lot and entry and exit_px is not None else ""
    good = res is not None and res > 0
    info = [(f"Result: {str(status).replace('_', ' ')}   {res:+.1f}% on premium{pnl}" if res is not None else f"Result: {status}",
             GREEN if good else RED),
            (f"Entry {entry} at {str(trade.get('actual_entry_time') or '')[11:16]}  ->  exit {exit_px} at {str(t_out or '')[11:16]}"
             f"   |   best move {trade.get('max_favorable_pct')}%, worst {trade.get('max_adverse_pct')}%", TXT)]
    sv = trade.get("sell_view") or {}
    if "pct" in sv:
        info.append((f"Seller view: selling the {sv.get('option')} over the same window would have made {sv['pct']:+.1f}% on premium", MUTE))
    return render(cs, path, f"{sym} {strike} {trade.get('direction')}  -  trade result",
                  f"{strategy or trade.get('strategy') or ''}   |   option premium, 1-minute chart",
                  levels=levels, vlines=[(trade.get("alert_time"), "Signal", MUTE)],
                  points=[(trade.get("actual_entry_time"), entry, "Entry", BLUE), (t_out, exit_px, "Exit", GREEN if good else RED)],
                  info=info, auto_levels=False, patterns=False)


def temp_png(stem: str) -> str:
    return os.path.join(tempfile.mkdtemp(), f"{stem}.png")
