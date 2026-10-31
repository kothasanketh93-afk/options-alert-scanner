"""
Signal Lab (2026-10-31) -- UNDERLYING-LEVEL data collection for many published strategies.
------------------------------------------------------------------------------
Purpose: collect live, out-of-sample evidence for several common intraday rule sets side by
side so the choice of strategy can be made later from results (user's request). Pure
observation: never creates trades, never sends Telegram, never touches shadow trades.

Once per day (first run at/after 15:30 IST) it evaluates each strategy on NIFTY and BANKNIFTY
5-minute candles (today + previous-session tail for indicator warmup), takes the FIRST signal of
the day per strategy, enters at the next candle's open and resolves with stop 1.0 x ATR(14) and
target 1.5 / 2.0 x ATR or session end -- the same rules as research/ backtests. One row per
date|symbol|strategy|target is appended to ledger/signal_lab.csv on the state branch.

Results are on the UNDERLYING (index points / R), BEFORE option premium effects and costs.
"""
import csv
import io
from pathlib import Path

TARGETS = (1.5, 2.0)
LAST_SIGNAL_MINS = 14 * 60 + 5           # 14:05, as in the backtests
COLUMNS = ["key", "date", "symbol", "strategy", "direction", "signal_time", "entry_time",
           "entry_price", "atr", "target_mult", "outcome", "result_r", "exit_price",
           "gap_pts", "wide_prev_day"]


# ----------------------------------------------------------------- indicators (pure python)
def _ema(xs, span):
    k = 2.0 / (span + 1)
    out, prev = [], None
    for x in xs:
        prev = x if prev is None else x * k + prev * (1 - k)
        out.append(prev)
    return out


def _rma(xs, n):
    a = 1.0 / n
    out, prev = [], None
    for x in xs:
        prev = x if prev is None else x * a + prev * (1 - a)
        out.append(prev)
    return out


def _atr(c, n=14):
    tr = []
    for i, x in enumerate(c):
        if i == 0:
            tr.append(x["high"] - x["low"])
        else:
            pc = c[i - 1]["close"]
            tr.append(max(x["high"] - x["low"], abs(x["high"] - pc), abs(x["low"] - pc)))
    return _rma(tr, n)


def _mins(candle):
    ts = str(candle["timestamp"])
    return int(ts[11:13]) * 60 + int(ts[14:16])


def _date(candle):
    return str(candle["timestamp"])[:10]


def _quantile(xs, q):
    s = sorted(xs)
    if not s:
        return None
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _trend15(candles):
    """Per-5m-candle trend (+1/-1/0) from 15m EMA9/EMA21, 15m bars anchored 09:15, no lookahead."""
    bars, idx_bar = [], []
    cur_key, cur = None, None
    for i, c in enumerate(candles):
        key = (_date(c), (_mins(c) - 555) // 15)
        if key != cur_key:
            cur_key = key
            bars.append({"close": c["close"], "n": 0, "last_i": i})
            cur = bars[-1]
        cur["close"] = c["close"]
        cur["n"] += 1
        cur["last_i"] = i
        idx_bar.append(len(bars) - 1)
    closes = [b["close"] for b in bars]
    e9, e21 = _ema(closes, 9), _ema(closes, 21)
    tr = []
    for i, c in enumerate(candles):
        b = idx_bar[i]
        third = (_mins(c) - 555) % 15 == 10          # bar completes on this 5m candle
        use = b if third else b - 1
        if use < 0 or use >= len(bars):
            tr.append(0)
        else:
            tr.append(1 if e9[use] > e21[use] else -1)
    return tr


# ----------------------------------------------------------------- strategies
def _build(candles, off, prev, gap_dir):
    """prev = (H, L, C) of prior day or None. Returns dict of strategy -> (signal_index, direction)."""
    n = len(candles)
    o = [c["open"] for c in candles]
    h = [c["high"] for c in candles]
    l = [c["low"] for c in candles]
    cl = [c["close"] for c in candles]
    atr = _atr(candles)
    trend = _trend15(candles)
    e9, e21 = _ema(cl, 9), _ema(cl, 21)
    d = [cl[i] - cl[i - 1] if i else 0.0 for i in range(n)]
    rsi = []
    g, ls = _rma([max(x, 0.0) for x in d], 14), _rma([max(-x, 0.0) for x in d], 14)
    for i in range(n):
        rsi.append(100.0 if ls[i] == 0 else 100 - 100 / (1 + g[i] / ls[i]))
    macd = [a - b for a, b in zip(_ema(cl, 12), _ema(cl, 26))]
    mh = [a - b for a, b in zip(macd, _ema(macd, 9))]
    bu, bl, bw = [None] * n, [None] * n, [None] * n
    for i in range(19, n):
        w = cl[i - 19:i + 1]
        m = sum(w) / 20
        sd = (sum((x - m) ** 2 for x in w) / 19) ** 0.5          # sample std, like pandas
        bu[i], bl[i], bw[i] = m + 2 * sd, m - 2 * sd, (4 * sd) / m
    gap_ok = lambda dr: gap_dir is not None and gap_dir == dr
    tr_ok = lambda i, dr: trend[i] == dr

    res = {}

    def first(name, cond, start=3):
        for i in range(off + start, n - 1):
            if _mins(candles[i]) > LAST_SIGNAL_MINS:
                return
            dr = cond(i)
            if dr:
                res[name] = (i, dr)
                return

    hi15 = max(h[off:off + 3]) if n - off >= 3 else None
    lo15 = min(l[off:off + 3]) if n - off >= 3 else None

    def orb(flt_trend):
        def f(i):
            dr = 1 if cl[i] > hi15 else (-1 if cl[i] < lo15 else 0)
            if not dr or not gap_ok(dr):
                return 0
            if flt_trend and not tr_ok(i, dr):
                return 0
            return dr
        return f
    if hi15 is not None:
        first("orb15_gap", orb(False))
        first("orb15_gap_trend", orb(True))

    if prev:
        pH, pL, pC = prev[:3]
        P = (pH + pL + pC) / 3
        R1, S1 = 2 * P - pL, 2 * P - pH
        rng = pH - pL
        H3, L3, H4, L4 = pC + rng * 1.1 / 4, pC - rng * 1.1 / 4, pC + rng * 1.1 / 2, pC - rng * 1.1 / 2
        BC = (pH + pL) / 2
        TC = 2 * P - BC
        first("pivot_break", lambda i: 1 if (cl[i] > R1 and cl[i - 1] <= R1 and cl[i] > o[i]) else
              (-1 if (cl[i] < S1 and cl[i - 1] >= S1 and cl[i] < o[i]) else 0))

        def pdhl(i):
            dr = 1 if cl[i] > pH else (-1 if cl[i] < pL else 0)
            return dr if dr and gap_ok(dr) else 0
        first("pdhl_gap", pdhl)
        first("camarilla_h3l3", lambda i: 1 if (l[i] <= L3 and cl[i] > L3 and cl[i] > o[i]) else
              (-1 if (h[i] >= H3 and cl[i] < H3 and cl[i] < o[i]) else 0))
        first("camarilla_h4l4", lambda i: 1 if (cl[i] > H4 and cl[i - 1] <= H4) else
              (-1 if (cl[i] < L4 and cl[i - 1] >= L4) else 0))
        # CPR "narrow day" is decided by the caller (needs a history of widths) -> flag via prev[3]
        if len(prev) > 3 and prev[3]:
            first("cpr_narrow", lambda i: 1 if (cl[i] > TC and cl[i - 1] <= TC) else
                  (-1 if (cl[i] < BC and cl[i - 1] >= BC) else 0))

    def squeeze(i):
        p = i - 1
        if (h[p] - l[p]) > 0.6 * atr[p]:
            return 0
        dr = 1 if cl[i] > h[p] else (-1 if cl[i] < l[p] else 0)
        return dr if dr and gap_ok(dr) and tr_ok(i, dr) else 0
    first("squeeze_gap_trend", squeeze, 4)

    def engulf(i):
        po, pc = o[i - 1], cl[i - 1]
        dr = 1 if (pc < po and cl[i] > po and o[i] <= pc) else (-1 if (pc > po and cl[i] < po and o[i] >= pc) else 0)
        return dr if dr and gap_ok(dr) and tr_ok(i, dr) else 0
    first("engulf_gap_trend", engulf, 4)
    first("ema_cross_5m", lambda i: 1 if (e9[i - 1] <= e21[i - 1] and e9[i] > e21[i]) else
          (-1 if (e9[i - 1] >= e21[i - 1] and e9[i] < e21[i]) else 0))
    first("composite_ema_rsi_macd", lambda i: 1 if (cl[i] > e21[i] and rsi[i] > 50 and mh[i - 1] <= 0 < mh[i]) else
          (-1 if (cl[i] < e21[i] and rsi[i] < 50 and mh[i - 1] >= 0 > mh[i]) else 0))
    first("rsi_reversion", lambda i: 1 if (rsi[i - 1] < 30 <= rsi[i]) else (-1 if (rsi[i - 1] > 70 >= rsi[i]) else 0))

    bwmin = {}
    for i in range(n):
        w = [x for x in bw[max(0, i - 59):i + 1] if x is not None]
        bwmin[i] = _quantile(w, 0.2) if len(w) >= 20 else None

    def bsq(i):
        if bu[i] is None or bw[i - 1] is None or bwmin[i - 1] is None:
            return 0
        if bw[i - 1] <= bwmin[i - 1]:
            return 1 if cl[i] > bu[i] else (-1 if cl[i] < bl[i] else 0)
        return 0
    first("bollinger_squeeze", bsq, 4)

    def bfade(i):
        if bl[i] is None or bl[i - 1] is None:
            return 0
        return 1 if (cl[i - 1] < bl[i - 1] and cl[i] > bl[i]) else (-1 if (cl[i - 1] > bu[i - 1] and cl[i] < bu[i]) else 0)
    first("bollinger_fade", bfade, 4)

    def inside(i):
        m_, ins = i - 2, i - 1
        if not (h[ins] < h[m_] and l[ins] > l[m_]):
            return 0
        return 1 if cl[i] > h[m_] else (-1 if cl[i] < l[m_] else 0)
    first("inside_bar", inside, 4)

    def pull(i):
        if i < off + 12:
            return 0
        e = _ema(cl[:i + 1], 21)[-1]
        t = trend[i]
        if t == 1 and l[i] <= e and cl[i] > o[i] and cl[i] > e:
            return 1
        if t == -1 and h[i] >= e and cl[i] < o[i] and cl[i] < e:
            return -1
        return 0
    first("ema_pullback", pull, 12)
    return res, atr


def _resolve(candles, entry, dr, atr, tp):
    o = candles[entry]["open"]
    stop = o - dr * atr
    tgt = o + dr * tp * atr
    for j in range(entry, len(candles)):
        hi, lo = candles[j]["high"], candles[j]["low"]
        if dr == 1:
            if lo <= stop:
                return "SL", -1.0, stop
            if hi >= tgt:
                return "TARGET", tp, tgt
        else:
            if hi >= stop:
                return "SL", -1.0, stop
            if lo <= tgt:
                return "TARGET", tp, tgt
    last = candles[-1]["close"]
    return "EOD", dr * (last - o) / atr, last


def compute_rows(symbol, warm, today, prev, gap_dir=None, wide_prev_day=None, narrow_cpr=False):
    """
    warm: previous-session 5m candles (may be empty); today: today's COMPLETE 5m candles;
    prev: (H, L, C) of the prior day or None. Returns list of row dicts (one per strategy x target).
    """
    if len(today) < 8:
        return []
    candles = list(warm) + list(today)
    off = len(warm)
    gap = today[0]["open"] - prev[2] if prev else None
    if gap_dir is None and gap:
        gap_dir = 1 if gap > 0 else -1
    pv = (prev + (narrow_cpr,)) if prev else None
    signals, atr = _build(candles, off, pv, gap_dir)
    date = _date(today[0])
    rows = []
    for name, (i, dr) in sorted(signals.items()):
        entry = i + 1
        if entry >= len(candles) or atr[i] <= 0:
            continue
        for tp in TARGETS:
            outcome, r, exit_px = _resolve(candles, entry, dr, atr[i], tp)
            rows.append({
                "key": f"{date}|{symbol}|{name}|{tp}", "date": date, "symbol": symbol, "strategy": name,
                "direction": "CE" if dr == 1 else "PE", "signal_time": candles[i]["timestamp"],
                "entry_time": candles[entry]["timestamp"], "entry_price": round(candles[entry]["open"], 2),
                "atr": round(atr[i], 2), "target_mult": tp, "outcome": outcome, "result_r": round(r, 3),
                "exit_price": round(exit_px, 2), "gap_pts": round(gap, 2) if gap is not None else None,
                "wide_prev_day": wide_prev_day,
            })
    return rows


def append_rows(rows, path):
    """Idempotent append (skips keys already present). Returns number of rows added."""
    path = Path(path)
    existing = set()
    if path.exists():
        with open(path, newline="") as f:
            existing = {r["key"] for r in csv.DictReader(f)}
    new = [r for r in rows if r["key"] not in existing]
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    header = not path.exists() or path.stat().st_size == 0
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    if header:
        w.writeheader()
    w.writerows(new)
    with open(path, "a", newline="") as f:
        f.write(buf.getvalue())
    return len(new)
