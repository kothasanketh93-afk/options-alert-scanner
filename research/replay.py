"""Replay harness: re-run LEDGER trades over the archived 1-minute option candles with alternative exit rules, and say
whether each strategy has enough data to be trusted. READ-ONLY research tool: it never writes to the repo or the state branch.

    python research/replay.py                      # reads origin/state of the local clone (git show), NSE + MCX
    python research/replay.py --repo /tmp/w/repo --min-n 100

Pieces
  1. gate report    per strategy: n, win%, avg net%, Wilson 95% lower bound of win rate, verdict (ENOUGH / THIN / TOO_FEW).
  2. baseline check replays each trade with its own ledger levels; reports how often the replay reproduces final_status
                    (this is what proves the harness is faithful before any variant is trusted).
  3. variants       exit-rule what-ifs (tighter/wider SL, T1/T2 scale, break-even after T1, time stop) per strategy.

Rules/caveats: same-candle tie -> SL first (conservative). Entry = ledger entry_price at actual_entry_time. Costs = the ledger's
cost_pct_assumed. Candles exist only for days archived on the state branch, so only those days are replayed.
Many variants on a small sample = chance findings: treat any gain as a hypothesis for forward data, never as proof.
"""
import argparse, csv, gzip, io, json, math, subprocess, sys
from collections import defaultdict

SRC = {"NSE": ("ledger/outcomes.csv", "ledger/candles/{d}.json.gz"),
       "MCX": ("mcx/ledger/outcomes.csv", "mcx/ledger/candles/{d}.json.gz")}


def wilson_lo(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def verdict(n, lo, min_n=100):
    if n < 30:
        return "TOO_FEW"
    return "ENOUGH" if n >= min_n and lo > 0.5 else ("THIN" if n < min_n else "NO_EDGE")


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def replay(candles, entry_hm, entry, sl, t1, t2, *, sl_scale=1.0, t1_scale=1.0, t2_scale=1.0, be_after_t1=False,
           max_hold_min=None):
    """candles: sorted [(HH:MM,[o,h,l,c])]. Returns (status, exit_price). Scales apply to distance from entry."""
    stop = entry - (entry - sl) * sl_scale
    tg1 = entry + (t1 - entry) * t1_scale
    tg2 = entry + (t2 - entry) * t2_scale
    t1hit, last, h0 = False, entry, None
    for hm, (o, h, l, c) in candles:
        if hm < entry_hm:
            continue
        if h0 is None:
            h0 = int(hm[:2]) * 60 + int(hm[3:])
        last = c
        if l <= stop:                                   # SL checked first on ties
            return ("T1_THEN_SL" if t1hit else "SL_HIT"), stop
        if not t1hit and h >= tg1:
            t1hit = True
            if be_after_t1:
                stop = max(stop, entry)
        if h >= tg2:
            return "T2_HIT", tg2
        if max_hold_min is not None and int(hm[:2]) * 60 + int(hm[3:]) - h0 >= max_hold_min:
            return ("T1_HIT_THEN_EOD" if t1hit else "EOD_CLOSED"), c
    return ("T1_HIT_THEN_EOD" if t1hit else "EOD_CLOSED"), last


def net_pct(entry, exit_, cost):
    return (exit_ - entry) / entry * 100 - cost


VARIANTS = {
    "baseline (ledger levels)": {},
    "SL 0.75x tighter": {"sl_scale": 0.75},
    "SL 1.25x wider": {"sl_scale": 1.25},
    "T1 0.75x closer": {"t1_scale": 0.75},
    "T2 0.75x closer": {"t2_scale": 0.75},
    "BE after T1": {"be_after_t1": True},
    "time stop 20m": {"max_hold_min": 20},
    "time stop 45m": {"max_hold_min": 45},
}


def git_show(repo, path):
    r = subprocess.run(["git", "show", "origin/state:" + path], capture_output=True, cwd=repo)
    return r.stdout if r.returncode == 0 else None


def load_master(repo, d, cache={}):
    if d in cache:
        return cache[d]
    raw = git_show(repo, f"instrument_master/{d}.json.gz")
    m = {}
    if raw:
        for r in json.loads(gzip.decompress(raw))["rows"]:
            if r.get("option_type") in ("CE", "PE") and r.get("strike_price") is not None:
                m[(r["underlying_symbol"], str(r["expiry"])[:10], float(r["strike_price"]), r["option_type"])] = r["instrument_key"]
    cache[d] = m
    return m


def load_state_keys(repo, prefix, d, cache={}):
    """trade_id -> instrument_key from <prefix>state/<date>.json (the day's own state file)."""
    ck = (prefix, d)
    if ck not in cache:
        raw = git_show(repo, f"{prefix}state/{d}.json")
        st = json.loads(raw) if raw else {}
        tr = st.get("trades") or st.get("mcx_shadow_trades") or {}
        tr = tr.values() if isinstance(tr, dict) else tr
        cache[ck] = {x.get("trade_id"): x.get("instrument_key") for x in tr if x.get("instrument_key")}
    return cache[ck]


def key_for(master, t):
    try:
        return master.get((t["symbol"], t["expiry"][:10], float(t["strike"]), t["direction"]))
    except (KeyError, TypeError, ValueError):
        return None


def collect(repo, ledger_rows, candle_loader, master_loader, trade_key_loader=lambda d: {}):
    """-> (trades with candles attached, counts). Pure function; loaders are injected so tests need no git."""
    out, miss_key, miss_c = [], 0, 0
    for t in ledger_rows:
        if t.get("final_status") in ("NOT_ENTERED", "", None):
            continue
        e, s, a, b = (f(t.get(k)) for k in ("entry_price", "stop_loss", "target_1", "target_2"))
        if None in (e, s, a, b) or e <= 0 or e <= s:
            continue
        day = candle_loader(t["date"])
        if day is None:
            miss_c += 1
            continue
        k = trade_key_loader(t["date"]).get(t.get("trade_id")) or key_for(master_loader(t["date"]), t)
        if not k or k not in day:
            miss_key += 1
            continue
        out.append({**t, "_c": sorted(day[k].items()), "_e": e, "_s": s, "_t1": a, "_t2": b})
    return out, {"no_key_or_candles": miss_key, "no_day_archive": miss_c}


def summarize(trades, **rule):
    by = defaultdict(list)
    for t in trades:
        hm = (t.get("actual_entry_time") or t["alert_time"])[11:16]
        st, ex = replay(t["_c"], hm, t["_e"], t["_s"], t["_t1"], t["_t2"], **rule)
        by[t["strategy"]].append((st, net_pct(t["_e"], ex, f(t.get("cost_pct_assumed")) or 0.0), t["final_status"]))
    return by


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--min-n", type=int, default=100)
    a = ap.parse_args(argv)
    for eng, (lp, cp) in SRC.items():
        raw = git_show(a.repo, lp)
        if not raw:
            print(f"[{eng}] no ledger"); continue
        rows = list(csv.DictReader(io.StringIO(raw.decode())))
        def cl(d, cp=cp):
            r = git_show(a.repo, cp.format(d=d))
            return json.loads(gzip.decompress(r)) if r else None
        trades, miss = collect(a.repo, rows, cl, lambda d: load_master(a.repo, d),
                              lambda d, p=("mcx/" if eng == "MCX" else ""): load_state_keys(a.repo, p, d))
        print(f"\n===== {eng}: {len(rows)} ledger rows, {len(trades)} replayable, skipped {miss} =====")
        base = summarize(trades)
        agree = sum(1 for v in base.values() for st, _, led in v if st == led)
        tot = sum(len(v) for v in base.values())
        print(f"baseline reproduces ledger final_status: {agree}/{tot} = {100*agree/max(tot,1):.0f}%")
        print("\n-- gate (win = net>0)  strategy | n | win% | wilson95_lo | avg_net% | verdict")
        for s, v in sorted(base.items(), key=lambda kv: -len(kv[1])):
            n = len(v); w = sum(1 for _, p, _ in v if p > 0)
            print(f"  {s:18s} {n:4d} {100*w/n:5.1f} {100*wilson_lo(w,n):6.1f} {sum(p for _,p,_ in v)/n:+7.2f}  {verdict(n, wilson_lo(w,n), a.min_n)}")
        print("\n-- variants (all strategies pooled)  variant | n | win% | avg_net% | total_net%")
        for name, rule in VARIANTS.items():
            v = [x for l in summarize(trades, **rule).values() for x in l]
            if v:
                n = len(v); w = sum(1 for _, p, _ in v if p > 0)
                print(f"  {name:26s} {n:4d} {100*w/n:5.1f} {sum(p for _,p,_ in v)/n:+7.2f} {sum(p for _,p,_ in v):+9.1f}")


if __name__ == "__main__":
    main()
