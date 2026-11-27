"""Offline exit-rule replay (added 2026-11-27). Research tool only: nothing here touches the live scanner.

Replays every closed trade on the archived 1-minute option candles (state branch: ledger/candles, mcx/ledger/candles) under
alternative exit rules (break-even after T1 / at +X%, half at T1, percentage trailing) and compares them to the rule actually used.

Conventions (same conservative posture as tracker.py): entry candle is not judged; a stop is checked against its value BEFORE the
candle's own high lifts it; a candle touching both the stop and a target counts as the stop (worst case); result % is the
option price move on the tracked entry, before costs (round-trip cost is subtracted in the summary).

Usage: python scripts/exit_replay.py --state-dir /path/to/options-scanner-state [--min-date 2026-11-13]
"""
import argparse
import glob
import gzip
import json
import os
import statistics as st

COST = 1.61   # assumed round-trip cost, % of premium (config.LEDGER_ROUND_TRIP_COST_PCT)


def _walk(o, out):
    if isinstance(o, dict):
        if o.get("instrument_key") and o.get("tracked_entry_price") and o.get("target_2") and o.get("actual_entry_time"):
            out.append(o)
        else:
            for v in o.values():
                _walk(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk(v, out)


def load_trades(state_dir, min_date="0000"):
    seen, rows = set(), []
    for sub, cdir in (("state", "ledger/candles"), ("mcx/state", "mcx/ledger/candles")):
        for f in sorted(glob.glob(os.path.join(state_dir, sub, "*.json"))):
            date = os.path.basename(f)[:10]
            if date < min_date:
                continue
            try:
                s = json.load(open(f))
            except Exception:
                continue
            cp = os.path.join(state_dir, cdir, date + ".json.gz")
            if not os.path.exists(cp):
                continue
            try:
                candles = json.load(gzip.open(cp, "rt"))
            except Exception:
                continue
            found = []
            _walk(s, found)
            for t in found:
                tid = (date, t.get("trade_id") or t.get("instrument_key"), t.get("strategy"), t.get("actual_entry_time"))
                if tid in seen or str(t.get("strategy", "")).endswith("_wsl"):
                    continue
                seen.add(tid)
                c = candles.get(t["instrument_key"])
                if c:
                    rows.append((date, t, c))
    return rows


def _pct(entry, p):
    return (p - entry) / entry * 100.0


def replay(trade, candles, rule):
    """rule: dict(be_after_t1, be_at_pct, half_t1, trail_pct, trail_from ('t1'|pct float), time_stop_min).
    Returns (result_pct, outcome, t2_hit)."""
    entry = trade["tracked_entry_price"]
    sl0 = trade.get("initial_stop_loss") or trade["stop_loss"]
    t1, t2 = trade["target_1"], trade["target_2"]
    hh, mm = trade["actual_entry_time"][11:16].split(":")
    start = f"{int(hh):02d}:{int(mm):02d}"
    keys = sorted(k for k in candles if k > start)
    if not keys:
        return None
    sl, hi, t1_hit, mfe = sl0, entry, False, 0.0
    booked = 0.0          # half booked at T1 (as % move), weight 0.5
    for k in keys:
        o, h, l, c = candles[k]
        # stop in force for this candle = value before this candle's own high
        hit_sl = l <= sl
        hit_t2 = h >= t2
        hit_t1 = (not t1_hit) and h >= t1
        if hit_sl and (hit_t1 or hit_t2):
            return _fin(_pct(entry, sl), "SL", rule, booked, t1_hit)
        if hit_sl:
            return _fin(_pct(entry, sl), "SL" if not t1_hit else "T1_SL", rule, booked, t1_hit)
        if hit_t1:
            t1_hit = True
            if rule.get("half_t1"):
                booked = _pct(entry, t1)
            if rule.get("be_after_t1") or rule.get("half_t1"):
                sl = max(sl, entry)
        if hit_t2:
            return _fin(_pct(entry, t2), "T2", rule, booked, t1_hit)
        hi = max(hi, h)
        mfe = max(mfe, _pct(entry, hi))
        if rule.get("be_at_pct") is not None and mfe >= rule["be_at_pct"]:
            sl = max(sl, entry)
        tp = rule.get("trail_pct")
        if tp:
            tf = rule.get("trail_from", "t1")
            on = t1_hit if tf == "t1" else mfe >= tf
            if on:
                sl = max(sl, hi * (1 - tp / 100.0))
    last = candles[keys[-1]][3]
    return _fin(_pct(entry, last), "EOD", rule, booked, t1_hit)


def _fin(res, outcome, rule, booked, t1_hit):
    if rule.get("half_t1") and t1_hit:
        res = 0.5 * booked + 0.5 * res
    return res, outcome


RULES = {
    "0 current rule (SL/T1/T2, no stop move)": {},
    "1 BE after T1": {"be_after_t1": True},
    "2 half at T1 + BE": {"half_t1": True},
    "3 BE at +6%": {"be_at_pct": 6.0},
    "4 BE at +10%": {"be_at_pct": 10.0},
    "5 BE at +15%": {"be_at_pct": 15.0},
    "6 trail 12% after T1 (BE floor)": {"be_after_t1": True, "trail_pct": 12.0, "trail_from": "t1"},
    "7 trail 8% after T1 (BE floor)": {"be_after_t1": True, "trail_pct": 8.0, "trail_from": "t1"},
    "8 trail 12% once +15%": {"trail_pct": 12.0, "trail_from": 15.0},
    "9 half at T1 + BE + trail 12%": {"half_t1": True, "trail_pct": 12.0, "trail_from": "t1"},
}


def summarize(rows, label=""):
    out = []
    for name, rule in RULES.items():
        rs, t2 = [], 0
        for date, t, c in rows:
            r = replay(t, c, rule)
            if r:
                rs.append(r[0])
                t2 += r[1] == "T2"
        if not rs:
            continue
        net = [x - COST for x in rs]
        out.append((name, len(rs), 100 * sum(1 for x in net if x > 0) / len(rs), st.mean(net), sum(net), t2))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--min-date", default="0000")
    a = ap.parse_args()
    rows = load_trades(a.state_dir, a.min_date)
    print(f"trades replayed: {len(rows)}")
    groups = {"ALL": rows,
              "NSE": [r for r in rows if r[1].get("symbol") not in ("CRUDEOIL", "NATURALGAS", "GOLDM", "GOLD")],
              "MCX": [r for r in rows if r[1].get("symbol") in ("CRUDEOIL", "NATURALGAS", "GOLDM", "GOLD")]}
    for g, rr in groups.items():
        if not rr:
            continue
        print(f"\n== {g} (n={len(rr)}) net of {COST}% cost ==")
        print(f"{'rule':44} {'n':>4} {'win%':>5} {'avg%':>6} {'sum%':>7} {'T2':>4}")
        for name, n, w, avg, tot, t2 in summarize(rr):
            print(f"{name:44} {n:4d} {w:5.1f} {avg:6.2f} {tot:7.1f} {t2:4d}")


if __name__ == "__main__":
    main()
