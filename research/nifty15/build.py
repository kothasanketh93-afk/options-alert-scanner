import pickle, numpy as np, pandas as pd
from report import *
from data import CFG
cov, days = pickle.load(open("meta.pkl","rb"))
T=pd.read_pickle("T.pkl"); S=pd.read_pickle("S.pkl"); C=pd.read_pickle("control.pkl"); Sn=pd.read_pickle("sens.pkl"); sk=pd.read_csv("skipped_base.csv")
base=T[(~T.regime_filter)&(T.sel=="ATM")]; cm=C[C.sel=="ATM"]
# capital feasibility
T1=T.copy(); feas=[]
for lot,g in T1[T1.sel=="ATM"].groupby("lot"):
    risk=g.risk_rupees.mean(); prem=g.entry.mean()
    for cap in (30000,50000):
        feas.append(dict(lot_size=lot,avg_entry_premium=round(prem,1),premium_cost_one_lot=round(prem*lot),stop_risk_one_lot_incl_charges=round(risk),
                         capital=cap,allowed_risk_0_5pct=cap*0.005,risk_as_pct_of_capital=round(risk/cap*100,2),eligible_under_0_5pct_rule=bool(risk<=cap*0.005),
                         min_capital_for_0_5pct_rule=round(risk/0.005),min_capital_for_1pct=round(risk/0.01)))
F=pd.DataFrame(feas)
# bootstrap
bs=[]
for (s,f,sel),t in T.groupby(["strategy","regime_filter","sel"]):
    b=boot(t); b.update(strategy=s,regime_filter=f,sel=sel); bs.append(b)
B=pd.DataFrame(bs)
bi=base.groupby("strategy").apply(lambda t: pd.Series(dict(trades=len(t),exp=round(t.net.mean(),1),random_exp=round(cm.exp.mean(),1),
        z_vs_random=round((t.net.mean()-cm.exp.mean())/(t.net.std(ddof=1)/np.sqrt(len(t))),2) if len(t)>2 else np.nan,hit=round((t.kind=="TARGET").mean()*100,1),random_hit=round(cm.hit.mean(),1)))).reset_index()
# ranking (baseline ATM, no filter) by validation+OOS combined expectancy w/ flags
vo=T[(T.sel=="ATM")&(T.split!="DEV(60%)")]
rk=[]
for (s,f),t in vo.groupby(["strategy","regime_filter"]):
    m=metrics(t); rk.append(dict(strategy=s,regime_filter=f,trades_valid_plus_oos=m.get("trades"),target_hit_rate=m.get("target_hit_rate"),stop_loss_rate=m.get("sl_rate"),
        net_pnl=m.get("net_pnl"),profit_factor=m.get("profit_factor"),max_drawdown=m.get("max_drawdown"),pos_months_pct=m.get("pos_months_pct"),
        flag="INSUFFICIENT SAMPLE (<100)" if (m.get("trades") or 0)<100 else ""))
R=pd.DataFrame(rk).sort_values("net_pnl",ascending=False); R.insert(0,"rank",range(1,len(R)+1))
def g2(by): 
    rows=[]
    for k,t in T.groupby(by,observed=True):
        m=metrics(t); m.update({(by if isinstance(by,str) else "+".join(by)):k}); rows.append(m)
    return pd.DataFrame(rows)
sel_cols=["trades","target_hit_rate","sl_rate","net_pnl","profit_factor","expectancy","max_drawdown"]
def grp2(cols):
    rows=[]
    for k,t in T.groupby(cols,observed=True):
        k=k if isinstance(k,tuple) else (k,)
        m=metrics(t); r=dict(zip(cols,k)); r.update({c:m.get(c) for c in sel_cols}); rows.append(r)
    return pd.DataFrame(rows)
dd=[]
for (s,f,sel),t in T.groupby(["strategy","regime_filter","sel"]):
    t=t.sort_values(["date","entry_min"]); eq=t.net.cumsum(); peak=eq.cummax(); dep=eq-peak
    end=dep.idxmin() if len(dep) else None
    dd.append(dict(strategy=s,regime_filter=f,sel=sel,max_drawdown=round(-dep.min()) if len(dep) else 0,trades_in_longest_dd=int((dep<0).astype(int).groupby((dep>=0).cumsum()).sum().max()) if len(dep) else 0,final_equity=round(eq.iloc[-1]) if len(eq) else 0))
D=pd.DataFrame(dd)
exe=pd.DataFrame({"finding":[
 "Period 2024-10-31..2026-10-30, 493 sessions, NIFTY weekly options, actual expired premiums (Upstox), 5m candles, 1 lot, 1 trade/day/strategy.",
 "Rule tested: target +15 / stop -10 option premium points, entry next option candle open + assumed slippage, flat 15:15.",
 f"RANDOM-ENTRY CONTROL (30 seeds, same exits/costs): target-hit {cm.hit.mean():.1f}% +/- {cm.hit.std():.1f}, expectancy Rs {cm.exp.mean():.0f} per trade, ~{cm.trades.mean():.0f} trades. Pure chance loses money after costs.",
 "No strategy beats the random-entry control by a statistically meaningful margin (all |z| < 1.3 on the baseline).",
 "Break-even target-hit rate under +15/-10 is 40% before costs and about 45% after costs/slippage (measured: avg target trade +Rs 949, avg stop trade -Rs 773); baseline strategies hit 31-38%.",
 "Only small-sample variants look good (S2 with regime filter: 28 trades, 53.6% hit, +Rs 5,163; S3 filtered: 14 trades). Not statistically supportable.",
 "Rs 30,000 / Rs 50,000 capital cannot satisfy the 0.5% risk rule for one lot (stop risk ~Rs 800 incl. charges = ~1.4-2.7% of capital for 65/75-lot periods).",
 "VWAP built from NIFTY futures volume; sector composite from 5 sector indices (approximate NIFTY weights). Delta 0.55-0.65 selection NOT possible (no Greeks). No bid/ask: slippage is an assumption."]})
asm=pd.DataFrame({"assumption":[
 "Slippage per side = max(0.5 pts, 0.4% of price) -- ASSUMED (no historical bid/ask).","Target fill requires price to trade 0.5 pts through the target.",
 "Same-candle target+stop => stop (adverse). Gap through stop => exit at the open.","Charges: Rs20/order, STT 0.10% sell (0.15% from 2026-04-01 ASSUMED), NSE 0.03503%, SEBI, stamp 0.003% buy, GST 18% -- verify.",
 "Sector composite weights approximate: Bank .35, IT .12, Energy .12, FMCG .08, Auto .07.","Weekly expiry = nearest expiry >= trade date; expiry-day sessions tagged separately.",
 "Regime filter: trend-following strategies only in TRENDING, reversal strategies (S5,S9) only in SIDEWAYS/NEUTRAL; HIGH_VOL excluded from both.",
 "S4 first-pullback produced only 15 trades; its extension limit was widened from 2.5 to 4.0 ATR BEFORE any P&L was inspected because the first version yielded 5 signals.",
 "Splits: first 60% of sessions = development, next 20% = validation, last 20% = OOS. Rules were frozen, nothing optimised; sensitivity table is diagnostic only.",
 "Lot sizes taken from the actual contracts (25 until Nov-2024, 75, then 65 from Jan-2026).","3 weekly expiries had no option file; those days are skipped (listed in Skipped sheet).",
 "Strikes exported on a 50-pt grid near each week's traded range; some signals fall outside -> 'no option file' skip."]})
with pd.ExcelWriter("NIFTY_15_10_options_research.xlsx") as x:
    exe.to_excel(x,sheet_name="Executive Summary",index=False); cov.to_excel(x,sheet_name="Data Coverage",index=False)
    R.to_excel(x,sheet_name="Strategy Comparison",index=False); S.to_excel(x,sheet_name="Individual Results",index=False)
    T.to_excel(x,sheet_name="Trade Log",index=False); grp2(["strategy","month"]).to_excel(x,sheet_name="Monthly Results",index=False)
    grp2(["strategy","dir"]).to_excel(x,sheet_name="CE vs PE",index=False); grp2(["strategy","sel"]).to_excel(x,sheet_name="ATM vs ITM",index=False)
    grp2(["strategy","expiry_day"]).to_excel(x,sheet_name="Expiry vs Non-Expiry",index=False)
    grp2(["strategy","regime"]).to_excel(x,sheet_name="Regime Analysis",index=False); grp2(["strategy","regime_filter"]).to_excel(x,sheet_name="Regime Filter On-Off",index=False)
    D.to_excel(x,sheet_name="Drawdown Analysis",index=False); grp2(["strategy","tod"]).to_excel(x,sheet_name="Time of Day",index=False)
    F.to_excel(x,sheet_name="Capital Feasibility",index=False); B.to_excel(x,sheet_name="Bootstrap CI",index=False)
    bi.to_excel(x,sheet_name="Vs Random Control",index=False); C.to_excel(x,sheet_name="Random Control Runs",index=False)
    Sn.to_excel(x,sheet_name="Sensitivity",index=False); sk.groupby(["strategy","reason"]).size().reset_index(name="count").to_excel(x,sheet_name="Skipped",index=False)
    asm.to_excel(x,sheet_name="Assumptions",index=False)
charts(T,".")
pd.set_option("display.width",250); pd.set_option("display.max_columns",30)
print(R.head(8).to_string()); print(F.to_string()); print(bi.to_string())
print(B[(B.sel=="ATM")&(~B.regime_filter)][["strategy","n","hit_rate","hit_ci_lo","hit_ci_hi","mean_net","mean_ci_lo","mean_ci_hi","p_mean_gt0"]].to_string())
print("weeks with a trade (S1..): ", base.groupby("strategy").week.nunique().to_dict(), "total weeks", T.week.nunique())
