"""Data loading, validation and indicator engine."""
import glob, gzip, json, os
import numpy as np, pandas as pd

BASE = "/tmp/claude-0/n15"
CFG = json.load(open(f"{BASE}/config.json"))

def wilder(x, n): return pd.Series(x).ewm(alpha=1/n, adjust=False).mean().values

def load_spot():
    df = pd.read_csv("/tmp/ra/history/NIFTY_5m.csv.gz")
    df["ts"] = df.timestamp.str[:19]; df["date"] = df.ts.str[:10]
    df["m"] = df.ts.str[11:13].astype(int)*60 + df.ts.str[14:16].astype(int)
    cnt = df.groupby("date").size(); df = df[df.date.isin(cnt[cnt >= 60].index)].reset_index(drop=True)
    pc = df.close.shift(1)
    tr = np.maximum.reduce([df.high-df.low, (df.high-pc).abs().fillna(0), (df.low-pc).abs().fillna(0)])
    df["atr"] = wilder(tr, 14)
    df["e9"] = df.close.ewm(span=9, adjust=False).mean(); df["e21"] = df.close.ewm(span=21, adjust=False).mean()
    up = df.high.diff().clip(lower=0).fillna(0).values; dn = (-df.low.diff()).clip(lower=0).fillna(0).values
    up, dn = np.where(up > dn, up, 0), np.where(dn > up, dn, 0)
    a = wilder(tr, 14)
    pdi, mdi = 100*wilder(up, 14)/np.maximum(a, 1e-9), 100*wilder(dn, 14)/np.maximum(a, 1e-9)
    df["adx"] = wilder(100*np.abs(pdi-mdi)/np.maximum(pdi+mdi, 1e-9), 14)
    return df

def load_futures():
    """continuous series: for each date use the first expiry at least 3 days away (rolls before expiry)."""
    fr = {}
    for f in sorted(glob.glob(f"{BASE}/extras/NIFTYFUT_*.csv.gz")):
        tag = os.path.basename(f)[9:-7]
        d = pd.read_csv(f); d["ts"] = d.timestamp.str[:19]; d["date"] = d.ts.str[:10]
        fr[tag] = d
    exps = sorted(k for k in fr if k != "live")
    out = []
    dates = sorted({x for d in fr.values() for x in d.date.unique()})
    for day in dates:
        cands = [e for e in exps if (pd.Timestamp(e) - pd.Timestamp(day)).days >= 3] + ["live"]
        for pick in cands:                      # first contract (nearest expiry >=3 days away) that has data that day
            if pick in fr:
                g = fr[pick][fr[pick].date == day]
                if len(g):
                    out.append(g.assign(contract=pick)); break
    return pd.concat(out).drop_duplicates("ts").reset_index(drop=True)

def day_vwap(spot_day, fut):
    """futures VWAP converted to spot-equivalent using the current basis; NaN where futures are missing."""
    f = fut.set_index("ts")
    cv = cvv = 0.0; out = []
    for ts, c in zip(spot_day.ts, spot_day.close):
        if ts in f.index:
            r = f.loc[ts]; tp = (r.high+r.low+r.close)/3
            cv += tp*r.volume; cvv += r.volume
            vw = cv/cvv if cvv > 0 else r.close
            out.append(c - (r.close - vw))
        else:
            out.append(np.nan)
    return np.array(out)

def load_options():
    opt = {}
    for f in glob.glob(f"{BASE}/opt/*.csv.gz"):
        o = pd.read_csv(f)
        if not len(o): continue
        o["ts"] = o.timestamp.str[:19]
        for (k, t), g in o.groupby(["strike", "type"]):
            opt[(o.expiry.iloc[0], float(k), t)] = g.drop_duplicates("ts").set_index("ts")[["open", "high", "low", "close", "volume", "oi", "lot_size"]]
    return opt

def load_sectors():
    out = {}
    for f in glob.glob(f"{BASE}/extras/IDX_*_5m.csv.gz"):
        n = os.path.basename(f)[4:-10].replace("_", " ")
        d = pd.read_csv(f); d["ts"] = d.timestamp.str[:19]; d["date"] = d.ts.str[:10]
        out[n] = d
    return out

def coverage_report(spot, fut, opt, sectors):
    rows = []
    days = sorted(spot.date.unique()); days = [d for d in days if CFG["period"]["start"] <= d <= CFG["period"]["end"]]
    exps = sorted({k[0] for k in opt})
    rows.append(("NIFTY spot 5m", f"{len(days)} sessions in period", f"{spot.date.min()}..{spot.date.max()}", "OK; volume=0"))
    miss = [d for d in days if d not in set(fut.date)] if len(fut) else days
    rows.append(("NIFTY futures 5m (volume)", f"{len(days)-len(miss)}/{len(days)} sessions", "", f"missing {len(miss)} sessions"))
    no_exp = [d for d in days if not any(e >= d for e in exps)]
    rows.append(("Weekly option expiries", f"{len(exps)} files", f"{exps[0]}..{exps[-1]}", f"{len(no_exp)} days beyond coverage"))
    for n, d in sectors.items(): rows.append((f"Index {n}", f"{len(d)} candles", f"{d.date.min()}..{d.date.max()}", ""))
    return pd.DataFrame(rows, columns=["dataset", "coverage", "range", "notes"])
