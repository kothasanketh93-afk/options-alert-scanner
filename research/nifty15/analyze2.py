import pickle, numpy as np, pandas as pd
from data import *; from engine import *; from report import *
import strategies as S
cov, days = pickle.load(open("meta.pkl","rb"))
spot=load_spot(); fut=load_futures(); opt=load_options(); sec=load_sectors(); ctxs=build_contexts(spot,fut,sec)
# --- random-entry control (same exit rules, same execution): null hypothesis benchmark
def mk_ctrl(seed):
    def f(x):
        rng=np.random.default_rng(seed*100003+int(x["day"].replace("-",""))); n=len(x["c"])
        cand=[i for i in range(n-2) if 570<=x["m"][i]<=845]
        yield int(rng.choice(cand)), int(rng.choice([1,-1]))
    return f
rows=[]
for seed in range(30):
    Tc,_=run(spot,fut,sec,opt,ctxs,strategies={"CTRL":mk_ctrl(seed)})
    Tc=Tc[~Tc.regime_filter]
    for sel,t in Tc.groupby("sel"):
        rows.append(dict(seed=seed,sel=sel,trades=len(t),hit=(t.kind=="TARGET").mean()*100,sl=(t.kind=="SL").mean()*100,net=t.net.sum(),exp=t.net.mean()))
C=pd.DataFrame(rows); C.to_pickle("control.pkl")
print(C.groupby("sel")[["trades","hit","sl","net","exp"]].agg(["mean","std"]).round(2).to_string())
# --- parameter sensitivity (NOT optimization; reported only to test fragility)
sens=[]
for tp,sl in [(10,7),(12,8),(15,10),(20,10),(15,15),(25,15)]:
    Ts,_=run(spot,fut,sec,opt,ctxs,tp=tp,sl=sl); Ts=Ts[(~Ts.regime_filter)&(Ts.sel=="ATM")]
    for s,t in Ts.groupby("strategy"): sens.append(dict(strategy=s,target=tp,stop=sl,trades=len(t),hit=round((t.kind=="TARGET").mean()*100,1),net=round(t.net.sum()),exp=round(t.net.mean(),1)))
Sn=pd.DataFrame(sens); Sn.to_pickle("sens.pkl")
print(Sn.pivot_table(index="strategy",columns=["target","stop"],values="net").round(0).to_string())
