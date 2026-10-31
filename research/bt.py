import pandas as pd, numpy as np, itertools, sys
def load(name):
    d=pd.read_csv(f'history/{name}_5m.csv.gz',parse_dates=['timestamp'])
    d['timestamp']=d.timestamp.dt.tz_convert('Asia/Kolkata')
    d=d.drop_duplicates('timestamp').sort_values('timestamp').reset_index(drop=True)
    d['date']=d.timestamp.dt.date
    t=d.timestamp.dt.hour*60+d.timestamp.dt.minute
    d=d[(t>=555)&(t<=925)].reset_index(drop=True)   # 09:15..15:25 bar starts
    d['mins']=d.timestamp.dt.hour*60+d.timestamp.dt.minute
    # ATR14 (Wilder) on 5m, continuous
    pc=d.close.shift(1)
    tr=np.maximum(d.high-d.low,np.maximum((d.high-pc).abs(),(d.low-pc).abs()))
    d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    # 15m bars (09:15 anchored) -> EMA9/21/ then mapped to 5m without lookahead
    g=d.groupby('date')
    k=((d.mins-555)//15)
    d['b15']=d.date.astype(str)+'_'+k.astype(str)
    b=d.groupby('b15',sort=False).agg(c=('close','last'),end=('mins','max'),first=('mins','min'),date=('date','first'))
    b['ema9']=b.c.ewm(span=9,adjust=False).mean(); b['ema21']=b.c.ewm(span=21,adjust=False).mean()
    b['ema50']=b.c.ewm(span=50,adjust=False).mean()
    # a 15m bar is complete when the 5m bar with mins==first+10 closes
    d=d.join(b[['ema9','ema21','ema50']].rename(columns=lambda x:x+'_15'),on='b15')
    # shift so a 5m bar only sees COMPLETED 15m bars: use the previous 15m bar's values unless this is its last 5m bar
    last5=(d.mins-555)%15==10
    for c in ['ema9_15','ema21_15','ema50_15']:
        prev=d.groupby('date')[c].shift(3)  # value from previous 15m bar's period (3 5m-bars earlier)
        d[c+'_ok']=np.where(last5,d[c],prev)
    return d
def sim(day, i, dirn, atr, sl, tp, last_i):
    o=day['open'][i]; stop=o-dirn*sl*atr; tgt=o+dirn*tp*atr; risk=sl*atr
    for j in range(i,last_i+1):
        hi,lo=day['high'][j],day['low'][j]
        if dirn==1:
            if lo<=stop: return -1.0
            if hi>=tgt: return tp/sl
        else:
            if hi>=stop: return -1.0
            if lo<=tgt: return tp/sl
    return dirn*(day['close'][last_i]-o)/risk
