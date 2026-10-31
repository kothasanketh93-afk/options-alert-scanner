import sys; sys.path.insert(0,'.')
from run import *
import numpy as np, pandas as pd
COST=0.10  # assumed option cost drag in R (unverified assumption)
def wide_flags(days):
    rng=[(a['prev'][0]-a['prev'][1]) if a['prev'] else np.nan for a in days]
    s=pd.Series(rng); avg=s.rolling(10,min_periods=5).mean().shift(0)  # avg of prior-day ranges up to prior day
    return (s>1.5*avg).to_numpy()
def orb(a,gapf,trendf):
    hi=a['high'][:3].max(); lo=a['low'][:3].min(); L=last_idx(a)
    if a['prev'] is None: return None
    gap=a['open'][0]-a['prev'][2]
    for i in range(3,L-1):
        c=a['close'][i]; d=1 if c>hi else (-1 if c<lo else 0)
        if d==0: continue
        if gapf and np.sign(gap)!=d: continue
        if trendf and trend15(a,i)!=d: continue
        return (i+1,d)
def pivot(a,gapf,trendf):
    if a['prev'] is None: return None
    P,R1,S1=levels(a['prev'],'classic'); L=last_idx(a); gap=a['open'][0]-a['prev'][2]
    for i in range(3,L-1):
        c,o,pc=a['close'][i],a['open'][i],a['close'][i-1]
        d=1 if (c>R1 and pc<=R1 and c>o) else (-1 if (c<S1 and pc>=S1 and c<o) else 0)
        if d==0: continue
        if gapf and np.sign(gap)!=d: continue
        if trendf and trend15(a,i)!=d: continue
        return (i+1,d)
VAR={'gap':(1,0,'any'),'gap+trend':(1,1,'any'),'gap+trend+normalday':(1,1,'normal'),'gap+trend+wideday':(1,1,'wide'),
     'trend':(0,1,'any'),'trend+normalday':(0,1,'normal'),'base':(0,0,'any')}
rows=[]
for n in ['NIFTY','BANKNIFTY']:
    days=day_arrays(load(n)); wf=wide_flags(days)
    for sname,fn,tp in [('ORB15',orb,1.5),('PIVOTBRK',pivot,2.0)]:
        for vn,(g,t,w) in VAR.items():
            for a,wide in zip(days,wf):
                if w=='normal' and wide: continue
                if w=='wide' and not wide: continue
                s=fn(a,g,t)
                if s is None: continue
                i,d=s; L=last_idx(a)
                if i>L: continue
                r=sim(a,i,d,a['atr'][i-1],1.0,tp,L)
                rows.append((n,sname,vn,a['date'],d,r))
R=pd.DataFrame(rows,columns=['idx','strat','var','date','dir','R']); R['date']=pd.to_datetime(R.date)
R['split']=np.where(R.date.dt.year<=2024,'train','test'); R['Rnet']=R.R-COST
R.to_pickle('./combo.pkl')
def st(x):
    n=len(x); m=x.mean(); t=m/(x.std()/np.sqrt(n)) if n>2 and x.std()>0 else np.nan
    return pd.Series({'n':n,'win%':(x>0).mean()*100,'gross_R':m,'net_R':m-COST,'t_net':(m-COST)/(x.std()/np.sqrt(n)) if n>2 else np.nan})
out=R.groupby(['strat','var','split']).R.apply(st).unstack().round(3)
print(out.to_string())
print('\nBy year, gap+trend, net:')
print(R[R['var']=='gap+trend'].groupby(['strat',R.date.dt.year]).Rnet.agg(['count','mean']).round(3).to_string())
