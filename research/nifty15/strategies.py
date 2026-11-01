"""
Ten baseline strategies with exact, frozen, programmatic rules (written BEFORE any result was seen).
All signals use the completed candle i; entry is the NEXT option candle. ctx is a dict of aligned numpy arrays:
o,h,l,c,m (minute of day),atr,e9,e21,adx,vw (spot-equivalent futures VWAP),fvol (futures volume),
pH,pL (previous day), h5,l5 (previous 5-day extremes), sec (sector-composite % change since prev close),
nchg (NIFTY % change since prev close).  dir: +1 = CE, -1 = PE.
"""
import numpy as np

W0, W1 = 9*60+30, 14*60+5

def ok(ctx, i): return W0 <= ctx["m"][i] <= W1 and not np.isnan(ctx["vw"][i])

def structure_up(h, l, i):
    return i >= 10 and h[i-5:i].max() > h[i-10:i-5].max() and l[i-5:i].min() > l[i-10:i-5].min()

def structure_dn(h, l, i):
    return i >= 10 and h[i-5:i].max() < h[i-10:i-5].max() and l[i-5:i].min() < l[i-10:i-5].min()

def s1_vwap_pullback(x):
    o, h, l, c, a, e9, e21, vw = (x[k] for k in ("o", "h", "l", "c", "atr", "e9", "e21", "vw"))
    for i in range(12, len(c)):
        if not ok(x, i) or x["m"][i] < 585: continue
        if abs(vw[i]-vw[i-6]) < 0.3*a[i]: continue                      # flat VWAP -> skip
        if c[i] > vw[i] and e9[i] > e21[i] and structure_up(h, l, i):
            if (l[i] <= vw[i]+0.25*a[i] or l[i] <= e21[i]+0.25*a[i]) and c[i] > o[i] and c[i] > e9[i]: yield i, 1
        elif c[i] < vw[i] and e9[i] < e21[i] and structure_dn(h, l, i):
            if (h[i] >= vw[i]-0.25*a[i] or h[i] >= e21[i]-0.25*a[i]) and c[i] < o[i] and c[i] < e9[i]: yield i, -1

def s2_orb_retest(x):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]
    if len(c) < 5: return
    hi, lo = h[:3].max(), l[:3].min(); st = None
    for i in range(3, len(c)):
        if x["m"][i] > W1: return
        body, rg = abs(c[i]-o[i]), max(h[i]-l[i], 1e-9)
        if st is None:
            if c[i] > hi and body >= .5*rg and c[i] > o[i]: st = (1, i)
            elif c[i] < lo and body >= .5*rg and c[i] < o[i]: st = (-1, i)
        else:
            d, bi = st
            if i-bi > 12: return
            if d == 1:
                if c[i] < hi: return
                if l[i] <= hi < c[i] and c[i] > o[i]: yield i, 1; return
            else:
                if c[i] > lo: return
                if h[i] >= lo > c[i] and c[i] < o[i]: yield i, -1; return

def s3_pdhl(x):
    o, h, l, c, vw, e9, e21 = (x[k] for k in ("o", "h", "l", "c", "vw", "e9", "e21")); pH, pL = x["pH"], x["pL"]; st = None
    for i in range(3, len(c)):
        if x["m"][i] > W1: return
        body, rg = abs(c[i]-o[i]), max(h[i]-l[i], 1e-9)
        if st is None:
            if c[i] > pH and body >= .5*rg and c[i] > o[i]: st = (1, i)
            elif c[i] < pL and body >= .5*rg and c[i] < o[i]: st = (-1, i)
        else:
            d, bi = st
            if i-bi > 12: return
            if d == 1:
                if c[i] < pH: return
                if l[i] <= pH < c[i] and c[i] > o[i] and not np.isnan(vw[i]) and c[i] > vw[i] and e9[i] > e21[i]: yield i, 1; return
            else:
                if c[i] > pL: return
                if h[i] >= pL > c[i] and c[i] < o[i] and not np.isnan(vw[i]) and c[i] < vw[i] and e9[i] < e21[i]: yield i, -1; return

def s4_first_pullback(x):
    o, h, l, c, a, fv = (x[k] for k in ("o", "h", "l", "c", "atr", "fvol"))
    for i in range(3, min(len(c)-2, 10)):
        imp = c[i]-o[0]
        if abs(imp) < 1.0*a[i]: continue
        cur, ref = np.nanmean(fv[:i+1]), np.nanmean(x["fvol_prev"][:i+1])
        if not (cur >= 1.2*ref): continue          # opening volume >= 1.2x previous session's same candles (futures)
        d = 1 if imp > 0 else -1
        for j in range(i+1, min(len(c)-1, i+8)):
            pb = c[j] < o[j] if d == 1 else c[j] > o[j]
            if pb:
                depth = (c[i]-l[j]) if d == 1 else (h[j]-c[i])
                if depth > .5*abs(imp): return
                k = j+1
                if x["m"][k] <= 10*60+45 and abs(c[k]-o[0]) < 4.0*a[k] and ((d == 1 and c[k] > h[j]) or (d == -1 and c[k] < l[j])):
                    yield k, d
                return
        return

def s5_sweep(x):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]; a = x["atr"]
    ohi, olo = h[:3].max(), l[:3].min()
    for i in range(12, len(c)):
        if x["m"][i] < 585 or x["m"][i] > W1: continue
        sh, sl = h[i-12:i-1].max(), l[i-12:i-1].min()
        body = abs(c[i]-o[i])
        for lvl in (x["pH"], ohi, sh):
            if h[i] > lvl+0.1*a[i] and c[i] < lvl and c[i] < o[i] and (h[i]-max(o[i], c[i])) >= body: yield i, -1; break
        else:
            for lvl in (x["pL"], olo, sl):
                if l[i] < lvl-0.1*a[i] and c[i] > lvl and c[i] > o[i] and (min(o[i], c[i])-l[i]) >= body: yield i, 1; break

def s6_vwap_reclaim(x):
    o, h, l, c, a, vw, e9 = (x[k] for k in ("o", "h", "l", "c", "atr", "vw", "e9"))
    st = None
    for i in range(6, len(c)):
        if not ok(x, i): continue
        body, rg = abs(c[i]-o[i]), max(h[i]-l[i], 1e-9)
        if st is None:
            if all(c[i-k] < vw[i-k] for k in (1, 2, 3)) and c[i] > vw[i] and c[i] > o[i] and body >= .5*rg: st = (1, i)
            elif all(c[i-k] > vw[i-k] for k in (1, 2, 3)) and c[i] < vw[i] and c[i] < o[i] and body >= .5*rg: st = (-1, i)
        else:
            d, bi = st
            if i-bi > 6: st = None; continue
            if d == 1:
                if c[i] < vw[i]: st = None; continue
                if i > bi and l[i] <= vw[i]+0.1*a[i] and c[i] > o[i] and c[i] > e9[i] and e9[i] >= e9[i-1]: yield i, 1; return
            else:
                if c[i] > vw[i]: st = None; continue
                if i > bi and h[i] >= vw[i]-0.1*a[i] and c[i] < o[i] and c[i] < e9[i] and e9[i] <= e9[i-1]: yield i, -1; return

def s7_sector(x):
    o, h, l, c, e21 = (x[k] for k in ("o", "h", "l", "c", "e21")); sec, nch = x["sec"], x["nchg"]
    for i in range(6, len(c)):
        if x["m"][i] < 600 or x["m"][i] > W1 or np.isnan(sec[i]): continue
        if sec[i] > 0.1 and sec[i] > nch[i]+0.05 and sec[i] >= sec[i-3] and c[i] > e21[i] and c[i] > o[0] and c[i] > o[i] and c[i] > h[i-1]: yield i, 1
        elif sec[i] < -0.1 and sec[i] < nch[i]-0.05 and sec[i] <= sec[i-3] and c[i] < e21[i] and c[i] < o[0] and c[i] < o[i] and c[i] < l[i-1]: yield i, -1

def s8_ema_pullback(x):
    o, h, l, c, a, e9, e21 = (x[k] for k in ("o", "h", "l", "c", "atr", "e9", "e21"))
    for i in range(12, len(c)):
        if x["m"][i] < 585 or x["m"][i] > W1: continue
        if e9[i] > e21[i] and structure_up(h, l, i) and (l[i] <= e9[i] or l[i] <= e21[i]+0.1*a[i]) and c[i] > o[i] and c[i] > e9[i]: yield i, 1
        elif e9[i] < e21[i] and structure_dn(h, l, i) and (h[i] >= e9[i] or h[i] >= e21[i]-0.1*a[i]) and c[i] < o[i] and c[i] < e9[i]: yield i, -1

def s9_sr_reversal(x):
    o, h, l, c, a, vw = (x[k] for k in ("o", "h", "l", "c", "atr", "vw"))
    sup = [x["pL"], x["l5"], l[:3].min()]; res = [x["pH"], x["h5"], h[:3].max()]
    for i in range(3, len(c)):
        if x["m"][i] < 585 or x["m"][i] > W1 or np.isnan(vw[i]): continue
        body = abs(c[i]-o[i])
        for s in sup:
            if l[i] <= s+0.1*a[i] and c[i] > s and c[i] > o[i] and (min(o[i], c[i])-l[i]) >= body and (c[i] >= vw[i] or vw[i]-c[i] <= 1.0*a[i]):
                opp = [r for r in res if r > c[i]]
                if not opp or min(opp)-c[i] >= 1.5*a[i]: yield i, 1; break
        else:
            for r in res:
                if h[i] >= r-0.1*a[i] and c[i] < r and c[i] < o[i] and (h[i]-max(o[i], c[i])) >= body and (c[i] <= vw[i] or c[i]-vw[i] <= 1.0*a[i]):
                    opp = [s for s in sup if s < c[i]]
                    if not opp or c[i]-max(opp) >= 1.5*a[i]: yield i, -1; break

def s10_nr7_inside(x):
    o, h, l, c = x["o"], x["h"], x["l"], x["c"]
    rg = h-l
    for i in range(9, len(c)):
        if x["m"][i] < 585 or x["m"][i] > W1: continue
        m_, ins = i-2, i-1
        if h[ins] < h[m_] and l[ins] > l[m_] and rg[ins] <= rg[ins-6:ins+1].min():       # inside bar that is also NR7
            if c[i] > h[m_]: yield i, 1
            elif c[i] < l[m_]: yield i, -1

STRATS = {"S1 VWAP Pullback Continuation": s1_vwap_pullback, "S2 ORB Retest": s2_orb_retest,
          "S3 PDH/PDL Breakout Retest": s3_pdhl, "S4 First Pullback After Opening Momentum": s4_first_pullback,
          "S5 Liquidity Sweep / Failed Breakout": s5_sweep, "S6 VWAP Reclaim / Breakdown": s6_vwap_reclaim,
          "S7 Relative Strength / Sector Momentum": s7_sector, "S8 EMA 9/21 Trend Pullback": s8_ema_pullback,
          "S9 Support & Resistance Reversal": s9_sr_reversal, "S10 Inside Bar / NR7 Breakout": s10_nr7_inside}
REVERSAL = {"S5 Liquidity Sweep / Failed Breakout", "S9 Support & Resistance Reversal"}
