import pickle, pandas as pd, numpy as np
from report import *
cov, days = pickle.load(open("meta.pkl","rb"))
T = assign_split(pd.read_csv("trades_base.csv"), days)
T.to_pickle("T.pkl")
S = summary_table(T); S.to_pickle("S.pkl")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
b = S[(S.period=="ALL")][["strategy","regime_filter","sel","trades","target_hit_rate","sl_rate","eod_rate","net_pnl","profit_factor","expectancy","t_stat","max_drawdown","trades_per_week"]]
print(b.sort_values(["regime_filter","sel","net_pnl"],ascending=[True,True,False]).to_string())
