"""MCX daily and weekly one-lot reports (images). Same look as the NSE reports (eod_image / weekly_image); data from the
MCX shadow state (daily) and the MCX ledger (weekly). Shadow data collection only."""
import csv
from datetime import date

import config
import eod_image
import eod_report
import mcx_config as mc
import weekly_image
import weekly_report

ENGINES = tuple((f"mcx_{k}", v) for k, v in mc.STRATEGY_LABELS.items())


def _commodity(r) -> str:
    return mc.NAMES.get(r["t"].get("symbol"), r["t"].get("symbol"))


def render_daily(st: dict, path: str) -> bool:
    pseudo = {f"mcx_{k}": {tid: t for tid, t in st.get("mcx_shadow_trades", {}).items() if t.get("strategy") == k}
              for k in mc.STRATEGY_LABELS}
    return eod_image.render(pseudo, st["date"], path, lot_sizes=mc.LOT_MULTIPLIER, title="MCX daily trade report",
                            subtitle="Crude, Natural Gas, Gold, Gold Mini options  |  shadow data collection",
                            engines=ENGINES, split=("Commodity", _commodity))


def collect_week(ledger_path, any_date_in_week: str) -> dict:
    d0, d1 = weekly_report.week_bounds(date.fromisoformat(any_date_in_week))
    try:
        rows = [r for r in csv.DictReader(open(ledger_path)) if d0.isoformat() <= r["date"] <= d1.isoformat()]
    except FileNotFoundError:
        rows = []
    rows.sort(key=lambda r: (r["date"], r.get("alert_time") or ""))
    seen, trades = set(), []
    for r in rows:
        res, entry = weekly_report._f(r.get("result_pct")), weekly_report._f(r.get("entry_price"))
        lot = mc.LOT_MULTIPLIER.get(r["symbol"])
        if res is None or not entry or not lot:
            continue
        k = (r["date"], r["symbol"], r["direction"], r["strike"], r.get("alert_time"))
        if k in seen:
            continue
        seen.add(k)
        gross = entry * lot * res / 100.0
        net = gross - entry * lot * config.LEDGER_ROUND_TRIP_COST_PCT / 100.0
        variants, filled = weekly_report.exit_variants(r, res, entry)
        trades.append({"date": r["date"], "label": mc.STRATEGY_LABELS.get(r.get("strategy"), r.get("strategy")),
                       "symbol": r["symbol"], "dir": r["direction"], "strike": r["strike"], "status": r["final_status"],
                       "lot": lot, "entry": entry, "res": res, "gross": gross, "net": net,
                       "bucket": mc.entry_bucket(r.get("alert_time")), "commodity": mc.NAMES.get(r["symbol"], r["symbol"]),
                       "variants": variants, "assumed": filled})
    return {"start": d0.isoformat(), "end": d1.isoformat(), "trades": trades}


def summarize_week(data: dict) -> dict:
    tr, agg = data["trades"], weekly_report._agg
    days = sorted({t["date"] for t in tr})
    out = {"all": agg(tr), "days": [dict(date=d, **agg([t for t in tr if t["date"] == d])) for d in days],
           "engines": [dict(label=lb, **agg([t for t in tr if t["label"] == lb])) for lb in dict.fromkeys(t["label"] for t in tr)],
           "buckets": [dict(label=b, **agg([t for t in tr if t["bucket"] == b])) for b in mc.BUCKETS if any(t["bucket"] == b for t in tr)],
           "split": [(mc.NAMES[c], agg([t for t in tr if t["symbol"] == c])) for c in mc.COMMODITIES],
           "split_title": "COMMODITY"}
    known = [t for t in tr if all(v is not None for v in t["variants"].values())]
    out["assumed"] = sum(1 for t in known if t["assumed"])
    out["rules"] = {name: (round(sum(t["entry"] * t["lot"] * t["variants"][name] / 100.0 for t in known)), len(known))
                    for name in ("Original SL", "SL to entry after T1", "Half at T1, rest at entry-stop", "Running trail after T1")}
    ranked = sorted(tr, key=lambda t: t["gross"])
    out["worst"], out["best"] = ranked[:4], list(reversed(ranked[-4:]))
    return out


def render_weekly(ledger_path, any_date_in_week: str, path: str):
    """Returns the data dict (with start/end) if an image was written, else None."""
    data = collect_week(ledger_path, any_date_in_week)
    if not data["trades"]:
        return None
    data.update({"summary": summarize_week(data), "title": "MCX weekly trade report",
                 "subtitle": "Crude, Natural Gas, Gold, Gold Mini  |  shadow engines"})
    return data if weekly_image.render(data, path) else None
