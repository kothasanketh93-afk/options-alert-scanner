import sys; sys.path.insert(0,'.')
from bt import *
import itertools, collections
GRID=[(1.0,1.5),(1.0,2.0),(1.5,2.0),(1.5,3.0)]
def day_arrays(d):
    out=[]
    prev=None
    for dt,g in d.groupby('date',sort=True):
        if len(g)<60: prev=None if prev is None else prev; continue
        a={k:g[k].to_numpy() for k in ['open','high','low','close','mins','atr','ema9_15_ok','ema21_15_ok','ema50_15_ok']}
        a['date']=dt; a['n']=len(g)
        a['prev']=prev
        out.append(a)
        prev=(g.high.max(),g.low.min(),g.close.iloc[-1],g.open.iloc[0])
    return out
def trend15(a,i):
    e9,e21,e50=a['ema9_15_ok'][i],a['ema21_15_ok'][i],a['ema50_15_ok'][i]
    if np.isnan(e9) or np.isnan(e21): return 0
    return 1 if (e9>e21) else -1
def last_idx(a): 
    idx=np.where(a['mins']<=910)[0]; return idx[-1]
def sig_orb(a,N,flt):
    n_or=N//5; hi=a['high'][:n_or].max(); lo=a['low'][:n_or].min(); L=last_idx(a)
    for i in range(n_or,L-1):
        c=a['close'][i]
        d=1 if c>hi else (-1 if c<lo else 0)
        if d==0: continue
        if flt=='trend' and trend15(a,i)!=d: continue
        if flt=='gap' and a['prev'] is not None:
            gap=a['open'][0]-a['prev'][2]
            if np.sign(gap)!=d: continue
        return (i+1,d)
    return None
def sig_pdhl(a,flt):
    if a['prev'] is None: return None
    pdh,pdl=a['prev'][0],a['prev'][1]; L=last_idx(a)
    for i in range(3,L-1):
        c=a['close'][i]; d=1 if c>pdh else (-1 if c<pdl else 0)
        if d==0: continue
        if flt=='trend' and trend15(a,i)!=d: continue
        return (i+1,d)
    return None
def ema_series(x,span): 
    return pd.Series(x).ewm(span=span,adjust=False).mean().to_numpy()
def sig_pullback(a):
    e21=ema_series(a['close'],21); L=last_idx(a)
    for i in range(12,L-1):
        if a['mins'][i]>840: break
        t=trend15(a,i)
        if t==0: continue
        if t==1 and a['low'][i]<=e21[i] and a['close'][i]>a['open'][i] and a['close'][i]>e21[i]: return (i+1,1)
        if t==-1 and a['high'][i]>=e21[i] and a['close'][i]<a['open'][i] and a['close'][i]<e21[i]: return (i+1,-1)
    return None
def levels(prev,kind):
    H,Lw,C,_=prev; P=(H+Lw+C)/3; R=H-Lw
    if kind=='classic': return P, 2*P-Lw, 2*P-H     # P,R1,S1
    return P, P+0.382*R, P-0.382*R
def sig_pivot(a,kind,mode):
    if a['prev'] is None: return None
    P,R1,S1=levels(a['prev'],kind); L=last_idx(a)
    for i in range(3,L-1):
        h,l,c,o=a['high'][i],a['low'][i],a['close'][i],a['open'][i]
        if mode=='bounce':
            if l<=S1 and c>S1 and c>o: return (i+1,1)
            if h>=R1 and c<R1 and c<o: return (i+1,-1)
        else:
            pc=a['close'][i-1]
            if c>R1 and pc<=R1 and c>o: return (i+1,1)
            if c<S1 and pc>=S1 and c<o: return (i+1,-1)
    return None
def sig_tod(a,mode):
    if a['n']<70: return None
    i0=12   # 10:15 bar start index
    mv=a['close'][i0-1]-a['open'][0]
    if mv==0: return None
    d=int(np.sign(mv)); d=d if mode=='mom' else -d
    return (i0,d)
def run(d,name):
    days=day_arrays(d); res=[]
    fams=[]
    for N,f in itertools.product([15,30,60],['none','trend','gap']): fams.append((f'ORB{N}-{f}',lambda a,N=N,f=f:sig_orb(a,N,f)))
    for f in ['none','trend']: fams.append((f'PDHL-{f}',lambda a,f=f:sig_pdhl(a,f)))
    fams.append(('PULLBACK-EMA21',sig_pullback))
    for k,m in itertools.product(['classic','fib'],['bounce','break']): fams.append((f'PIVOT-{k}-{m}',lambda a,k=k,m=m:sig_pivot(a,k,m)))
    for m in ['mom','rev']: fams.append((f'TOD1h-{m}',lambda a,m=m:sig_tod(a,m)))
    rows=[]
    for fname,fn in fams:
        sigs=[(a,fn(a)) for a in days]
        for sl,tp in GRID:
            for a,s in sigs:
                if s is None: continue
                i,dirn=s; L=last_idx(a)
                if i>L: continue
                r=sim(a,i,dirn,a['atr'][i-1] if i>0 else a['atr'][0],sl,tp,L)
                rows.append((name,fname,sl,tp,a['date'],dirn,r))
    return pd.DataFrame(rows,columns=['idx','fam','sl','tp','date','dir','R'])
if __name__=='__main__':
    allr=[]
    for n in ['NIFTY','BANKNIFTY']:
        d=load(n); allr.append(run(d,n))
    R=pd.concat(allr); R['date']=pd.to_datetime(R.date)
    R.to_pickle('./results.pkl'); print(len(R),'trade rows')
