"""Weekly one-lot report from the outcomes ledger (Mon-Sun week containing a date). Educational/data tool, not advice.

P&L per closed trade = entry price x lot size x result% (lot size from that day's instrument master). Same contract and
alert time seen under several engines is counted once (LIVE first). 'After costs' uses config.LEDGER_ROUND_TRIP_COST_PCT.
"""
import csv
import gzip
import json
from datetime import date, timedelta
from pathlib import Path

import config
import eod_report

_PRIORITY = {"live": 0, "main": 1, "fib_pivot": 2, "orb_pivot": 3, "sweep_reversal": 4}
_BUCKETS = ("0915-0945", "0945-1130", "1130-1400", "1400+")
INDEX = {"NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY", "FINNIFTY"}
_LABEL = {"live": "LIVE", "main": "Main shadow", "fib_pivot": "Fib pivot", "orb_pivot": "ORB"}


def week_bounds(d: date):
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def _f(x):
    try:
        return None if x in ("", None) else float(x)
    except ValueError:
        return None


def _lots_for(master_dir: Path, day: str, cache: dict) -> dict:
    if day not in cache:
        p = Path(master_dir) / f"{day}.json.gz"
        cache[day] = eod_report.lot_sizes_from_gz(str(p)) if p.exists() else {}
    return cache[day]


def _label(r) -> str:
    eng = r["engine"]
    if eng == "sweep_reversal":
        return eod_report._SWEEP_LABELS.get(r.get("strategy"), "Sweep")
    if eng == "main" and r.get("strategy") == "chase_bypass":
        return "Chase bypass"
    return _LABEL.get(eng, eng)


def exit_variants(r: dict, res: float, entry: float):
    """What-if exit-rule results (percent) for one ledger row. Returns (variants, assumed_flag)."""
    t1 = r.get("t1_hit") in ("True", "true", "1")
    variants, filled = {}, False
    t1pct = ((_f(r.get("target_1")) - entry) / entry * 100) if _f(r.get("target_1")) else None
    status = r["final_status"]
    for name, col in (("SL to entry after T1", "be_after_t1_pct"), ("Half at T1, rest at entry-stop", "half_t1_plus_be_pct"),
                      ("Running trail after T1", "trail_after_t1_pct")):
        v = _f(r.get(col))
        if v is None and not t1:
            v = res
        elif v is None and t1 and t1pct is not None:
            # records without pullback markers: T2 hits assumed to have had no pullback to entry; T1-then-SL trades
            # necessarily fell back through entry (exit at entry, or better for the trail -- taken as entry).
            filled = True
            if status == "T2_HIT":
                v = {"SL to entry after T1": res, "Running trail after T1": res}.get(name, 0.5 * t1pct + 0.5 * res)
            elif status in ("T1_THEN_SL", "T1_THEN_TRAILING_SL", "TRAILING_SL_HIT"):
                v = {"SL to entry after T1": 0.0, "Running trail after T1": 0.0}.get(name, 0.5 * t1pct)
        variants[name] = v
    variants["Original SL"] = res
    return variants, filled


def collect(ledger_path, master_dir, any_date_in_week: str) -> dict:
    d0, d1 = week_bounds(date.fromisoformat(any_date_in_week))
    rows = [r for r in csv.DictReader(open(ledger_path)) if d0.isoformat() <= r["date"] <= d1.isoformat()]
    rows.sort(key=lambda r: (r["date"], _PRIORITY.get(r["engine"], 9), r.get("alert_time") or ""))
    cache, seen, trades = {}, set(), []
    for r in rows:
        res, entry = _f(r.get("result_pct")), _f(r.get("entry_price"))
        if res is None or not entry:
            continue
        k = (r["date"], r["symbol"], r["direction"], r["strike"], r.get("alert_time"))
        if k in seen:
            continue
        seen.add(k)
        lot = _lots_for(master_dir, r["date"], cache).get(r["symbol"])
        if not lot:
            continue
        gross = entry * lot * res / 100.0
        net = gross - entry * lot * config.LEDGER_ROUND_TRIP_COST_PCT / 100.0
        variants, filled = exit_variants(r, res, entry)
        trades.append({"date": r["date"], "label": _label(r), "symbol": r["symbol"], "dir": r["direction"],
                       "strike": r["strike"], "status": r["final_status"], "lot": lot, "entry": entry, "res": res,
                       "gross": gross, "net": net, "bucket": r.get("entry_bucket") or "",
                       "index": r["symbol"] in INDEX, "variants": variants, "assumed": filled})
    return {"start": d0.isoformat(), "end": d1.isoformat(), "trades": trades}


def _agg(sel):
    return {"n": len(sel), "wins": sum(1 for t in sel if t["status"] == "T2_HIT"),
            "losses": sum(1 for t in sel if t["gross"] < 0), "gross": round(sum(t["gross"] for t in sel)),
            "net": round(sum(t["net"] for t in sel))}


def summarize(data: dict) -> dict:
    tr = data["trades"]
    days = sorted({t["date"] for t in tr})
    out = {"all": _agg(tr), "days": [dict(date=d, **_agg([t for t in tr if t["date"] == d])) for d in days],
           "engines": [dict(label=lb, **_agg([t for t in tr if t["label"] == lb])) for lb in dict.fromkeys(t["label"] for t in tr)],
           "buckets": [dict(label=b, **_agg([t for t in tr if t["bucket"] == b])) for b in _BUCKETS if any(t["bucket"] == b for t in tr)],
           "index": _agg([t for t in tr if t["index"]]), "stock": _agg([t for t in tr if not t["index"]])}
    known = [t for t in tr if all(v is not None for v in t["variants"].values())]
    out["assumed"] = sum(1 for t in known if t["assumed"])
    out["rules"] = {name: (round(sum(t["entry"] * t["lot"] * t["variants"][name] / 100.0 for t in known)), len(known))
                    for name in ("Original SL", "SL to entry after T1", "Half at T1, rest at entry-stop", "Running trail after T1")}
    ranked = sorted(tr, key=lambda t: t["gross"])
    out["worst"], out["best"] = ranked[:4], list(reversed(ranked[-4:]))
    return out
