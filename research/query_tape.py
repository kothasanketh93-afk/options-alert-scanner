"""Query the pattern tape. Usage:
  python3 research/query_tape.py <dir_with_daily_csvs> [--strategy NAME] [--pattern NAME] [--symbol NIFTY] [--horizon 6]
Lists every bar where the strategy/pattern fired and what the underlying did over the next `horizon` bars
(default 6 = 30 min): signed forward move in points (CE/PE direction applied for strategy signals) and a hit rate."""
import argparse
import glob

import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("dir"); ap.add_argument("--strategy"); ap.add_argument("--pattern")
ap.add_argument("--symbol"); ap.add_argument("--horizon", type=int, default=6)
a = ap.parse_args()
df = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{a.dir}/*.csv"))], ignore_index=True).fillna("")
if a.symbol:
    df = df[df.symbol == a.symbol]
out = []
for (sym, d), g in df.groupby(["symbol", "date"]):
    g = g.sort_values("bar_time").reset_index(drop=True)
    for i, r in g.iterrows():
        dr = 0
        if a.strategy:
            hit = [s for s in str(r.signals_new).split(";") if s.startswith(a.strategy + ":")]
            if not hit:
                continue
            dr = 1 if hit[0].endswith("CE") else -1
        if a.pattern:
            if a.pattern not in str(r.patterns):
                continue
            if not a.strategy:
                dr = 1 if "(b)" in str(r.patterns) else (-1 if "(b)" not in str(r.patterns) and "(s)" in str(r.patterns) else 0)
        j = i + a.horizon
        fwd = (g.loc[j, "close"] - r.close) * (dr or 1) if j < len(g) else None
        out.append(dict(symbol=sym, date=d, bar=r.bar_time, direction=dr, patterns=r.patterns, fwd_pts=fwd))
res = pd.DataFrame(out)
print(res.to_string(index=False) if len(res) else "no matches")
if len(res) and res.fwd_pts.notna().any():
    v = res.fwd_pts.dropna()
    print(f"\nn={len(v)}  mean fwd move {v.mean():.1f} pts  positive {100*(v>0).mean():.0f}%")
