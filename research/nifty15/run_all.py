import pickle, time, sys
import pandas as pd
from data import *
from engine import *
t0=time.time()
spot=load_spot(); fut=load_futures(); opt=load_options(); sec=load_sectors()
print("loaded",round(time.time()-t0), "fut days", fut.date.nunique(), "opt contracts", len(opt), "sectors", list(sec))
cov=coverage_report(spot,fut,opt,sec); print(cov.to_string())
ctxs=build_contexts(spot,fut,sec); print("contexts",len(ctxs), round(time.time()-t0))
T,sk=run(spot,fut,sec,opt,ctxs); print("trades",len(T), round(time.time()-t0))
T.to_csv("trades_base.csv",index=False); sk.to_csv("skipped_base.csv",index=False)
pickle.dump((cov,sorted(ctxs)),open("meta.pkl","wb"))
print(T.groupby(["strategy","regime_filter","sel"]).size().unstack([1,2]).to_string())
print(sk.groupby("reason").size().to_string())
