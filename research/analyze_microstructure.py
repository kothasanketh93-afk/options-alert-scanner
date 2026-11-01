"""Analyse ledger/microstructure/*.csv (fetch the state branch first).
Usage: python3 research/analyze_microstructure.py <dir_with_daily_csvs>
Prints: (1) spread by symbol/DTE/ATM offset/time bucket, (2) round-trip spread cost in points and
rupees for a 1-lot buy, (3) theta per 5 minutes vs the +15/-10 target, (4) realistic-fill replay of
tracked shadow trades (buy at ask, sell at bid) from their snapshots."""
import glob
import sys

import pandas as pd

LOT = {"NIFTY": 65, "BANKNIFTY": 30}


def load(d):
    fs = sorted(glob.glob(f"{d}/*.csv"))
    if not fs:
        sys.exit("no csv files found")
    df = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    df["bucket"] = pd.cut(df.minute, [0, 600, 690, 780, 900, 1440],
                          labels=["09:15-10:00", "10:00-11:30", "11:30-13:00", "13:00-15:00", "15:00+"])
    return df


def spreads(df):
    q = df[df.spread_abs.notna() & (df.mid > 0)]
    print("\n== quote coverage: %.1f%% rows have a two-sided quote" % (100 * len(q) / len(df)))
    for by in (["symbol", "dte"], ["symbol", "atm_offset"], ["symbol", "bucket"]):
        t = q.groupby(by, observed=True).agg(n=("mid", "size"), mid=("mid", "median"),
                                              spread_pts=("spread_abs", "median"),
                                              spread_pct=("spread_pct", "median"),
                                              p90_pct=("spread_pct", lambda s: s.quantile(.9)))
        print("\n-- spread by", by); print(t.round(2).to_string())
    atm = q[q.atm_offset == 0].copy()
    atm["lot"] = atm.symbol.map(LOT)
    atm["rs_cost"] = atm.spread_abs * atm.lot      # buy at ask + sell at bid = one full spread
    print("\n-- ATM round-trip spread cost (Rs per lot, before brokerage/STT)")
    print(atm.groupby(["symbol", "bucket"], observed=True).rs_cost.describe()[["count", "50%", "mean"]].round(0).to_string())


def theta(df):
    a = df[(df.atm_offset == 0) & df.theta.notna()].copy()
    a["theta_5m"] = a.theta / 75.0       # per-day theta spread over ~75 five-minute bars
    print("\n== ATM theta per 5 minutes (points) by DTE")
    print(a.groupby(["symbol", "dte"]).theta_5m.median().round(2).to_string())


def replay(df):
    t = df[df.trade_ids.fillna("") != ""].copy()
    if t.empty:
        print("\n(no tracked shadow-trade snapshots yet)"); return
    t["trade_id"] = t.trade_ids.str.split(";")
    t = t.explode("trade_id").sort_values("ts")
    out = []
    for tid, g in t.groupby("trade_id"):
        g = g.dropna(subset=["bid", "ask"])
        if len(g) < 2: continue
        e, x = g.iloc[0], g.iloc[-1]
        out.append(dict(trade_id=tid, side=e.side, buy_at_ask=e.ask, sell_at_bid=x.bid,
                        pts=round(x.bid - e.ask, 2), entry_spread_pct=e.spread_pct, snaps=len(g)))
    print("\n== realistic-fill replay (first snapshot ask -> last snapshot bid; indicative only)")
    print(pd.DataFrame(out).to_string(index=False) if out else "not enough snapshots")


if __name__ == "__main__":
    d = load(sys.argv[1]); spreads(d); theta(d); replay(d)
