"""Backtest engine: contexts, regime classification, contract selection, execution, charges, trade log."""
import numpy as np, pandas as pd
from data import CFG, load_spot, load_futures, load_options, load_sectors, day_vwap
from strategies import STRATS, REVERSAL

TP, SLP = CFG["target_points"], CFG["stop_points"]
SL_CFG, CH = CFG["slippage"], CFG["charges"]

def slip(p): return max(SL_CFG["min_points"], SL_CFG["pct_of_price"]/100*p)

def charges(buy_val, sell_val, date):
    brok = 2*CH["brokerage_per_order"]
    stt = sell_val*(CH["stt_sell_pct_from_2026_04_01"] if date >= "2026-04-01" else CH["stt_sell_pct_before_2026_04_01"])/100
    txn = (buy_val+sell_val)*CH["nse_txn_pct"]/100; sebi = (buy_val+sell_val)*CH["sebi_pct"]/100
    return brok+stt+txn+sebi+buy_val*CH["stamp_buy_pct"]/100+CH["gst_pct"]/100*(brok+txn+sebi)

def build_contexts(spot, fut, sectors):
    days = sorted(spot.date.unique())
    daily = spot.groupby("date").agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"))
    atr_day = spot.groupby("date").atr.mean()
    fut_by = {d: g for d, g in fut.groupby("date")}
    wts = CFG["sector_weights"]; sec_by = {}
    for n in wts:
        d = sectors.get(n)
        if d is not None: sec_by[n] = {k: g.set_index("ts").close for k, g in d.groupby("date")}
    ctxs = {}
    for k, day in enumerate(days):
        if k < 6 or day < CFG["period"]["start"] or day > CFG["period"]["end"]: continue
        g = spot[spot.date == day].reset_index(drop=True)
        pr = daily.iloc[k-1]; last5 = daily.iloc[k-5:k]
        fg = fut_by.get(day)
        vw = day_vwap(g, fg) if fg is not None else np.full(len(g), np.nan)
        fvol = g.ts.map(fg.set_index("ts").volume).values if fg is not None else np.full(len(g), np.nan)
        # sector composite % change since previous close (no look-ahead; forward-filled within day)
        sec = np.zeros(len(g)); wsum = 0.0
        for n, w in wts.items():
            if n not in sec_by or day not in sec_by[n]: continue
            prevs = [d for d in sec_by[n] if d < day]
            if not prevs: continue
            pc = sec_by[n][max(prevs)].iloc[-1]
            s = g.ts.map(sec_by[n][day]).ffill().values
            sec += w*((s/pc-1)*100); wsum += w
        sec = sec/wsum if wsum else np.full(len(g), np.nan)
        pdays = [d for d in fut_by if d < day]
        pf = fut_by[max(pdays)].set_index("ts").volume.values if pdays else np.array([])
        fvp = np.full(len(g), np.nan); fvp[:min(len(g), len(pf))] = pf[:len(g)]
        x = dict(fvol_prev=fvp, day=day, ts=list(g.ts), o=g.open.values, h=g.high.values, l=g.low.values, c=g.close.values, m=g.m.values,
                 atr=g.atr.values, e9=g.e9.values, e21=g.e21.values, adx=g.adx.values, vw=vw, fvol=fvol,
                 pH=pr.h, pL=pr.l, h5=last5.h.max(), l5=last5.l.min(), sec=sec,
                 nchg=(g.close.values/pr.c-1)*100, atr_base=atr_day.iloc[k-5:k].mean())
        ctxs[day] = x
    return ctxs

def regime_at(x, i):
    R = CFG["regime"]; a = x["atr"][i]
    if a/x["atr_base"] >= R["atr_ratio_high_vol"]: return "HIGH_VOL"
    vw = x["vw"]
    if np.isnan(vw[i]) or i < 6 or np.isnan(vw[i-6]): return "NEUTRAL"
    slope = abs(vw[i]-vw[i-6])/a
    aligned = np.sign(x["e9"][i]-x["e21"][i]) == np.sign(x["c"][i]-vw[i])
    if x["adx"][i] >= R["adx_trending"] and aligned and slope >= R["vwap_slope_min_atr"]: return "TRENDING"
    if x["adx"][i] < R["adx_sideways"] or slope < R["vwap_slope_min_atr"]: return "SIDEWAYS"
    return "NEUTRAL"

def regime_ok(name, reg): return reg in ({"SIDEWAYS", "NEUTRAL"} if name in REVERSAL else {"TRENDING"})

def pick_contract(opt, expiries, day, spot, d, sel):
    exp = next((e for e in expiries if e >= day), None)
    if exp is None: return None
    atm = round(spot/50)*50
    strike = atm if sel == "ATM" else (atm-50 if d == 1 else atm+50)
    ser = opt.get((exp, float(strike), "CE" if d == 1 else "PE"))
    return None if ser is None else (exp, strike, ser)

def run(spot, fut, sectors, opt, ctxs, strategies=None, tp=TP, sl=SLP):
    expiries = sorted({k[0] for k in opt}); rows = []; skipped = []
    for day, x in ctxs.items():
        for name, fn in (strategies or STRATS).items():
            sigs = list(fn(x))
            for filt in (False, True):
                done = False
                for i, d in sigs:
                    if done: break
                    if not (570 <= x["m"][i] <= 845) or i+1 >= len(x["c"]): continue
                    reg = regime_at(x, i)
                    if filt and not regime_ok(name, reg): continue
                    for sel in ("ATM", "ITM1"):
                        pc = pick_contract(opt, expiries, day, x["c"][i], d, sel)
                        if pc is None: skipped.append((day, name, sel, "no option file for expiry/strike")); continue
                        exp, strike, ser = pc
                        if x["ts"][i+1] not in ser.index: skipped.append((day, name, sel, "no option candle at entry")); continue
                        e = ser.loc[x["ts"][i+1]]
                        if e.volume <= 0: skipped.append((day, name, sel, "illiquid (zero volume at entry)")); continue
                        lot = int(e.lot_size); entry = e.open + slip(e.open)
                        tgt, stp = entry+tp, entry-sl
                        px = kind = None; j_exit = i+1
                        for j in range(i+1, len(x["c"])):
                            t = x["ts"][j]
                            if t not in ser.index: continue
                            r = ser.loc[t]
                            if r.low <= stp or (j > i+1 and r.open <= stp):
                                px, kind, j_exit = min(r.open if j > i+1 else stp, stp) - slip(stp), "SL", j; break
                            if r.high >= tgt+CFG["target_fill_through_points"]:
                                px, kind, j_exit = tgt, "TARGET", j; break
                            if x["m"][j] >= 915:
                                px, kind, j_exit = r.close - slip(r.close), "EOD", j; break
                        if px is None: skipped.append((day, name, sel, "no exit data")); continue
                        gross = (px-entry)*lot; ch = charges(entry*lot, px*lot, day)
                        rows.append(dict(strategy=name, regime_filter=filt, sel=sel, date=day, dir="CE" if d == 1 else "PE",
                                         expiry=exp, expiry_day=(exp == day), strike=strike, lot=lot, entry=round(entry, 2),
                                         exit=round(px, 2), kind=kind, gross=round(gross, 1), charges=round(ch, 1),
                                         net=round(gross-ch, 1), pts=round(px-entry, 2), regime=reg, sig_min=int(x["m"][i]),
                                         entry_min=int(x["m"][i+1]), hold_min=int(x["m"][j_exit]-x["m"][i+1]+5),
                                         risk_rupees=round(sl*lot+charges(entry*lot, (entry-sl)*lot, day), 1),
                                         opt_vol=float(e.volume), opt_oi=float(e.oi)))
                    # one trade per day per (strategy, filter): stop after first signal that produced rows
                    if any(r["date"] == day and r["strategy"] == name and r["regime_filter"] == filt for r in rows[-2:]): done = True
    return pd.DataFrame(rows), pd.DataFrame(skipped, columns=["date", "strategy", "sel", "reason"])
