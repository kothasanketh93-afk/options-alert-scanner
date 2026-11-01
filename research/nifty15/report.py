"""Performance analytics, validation, bootstrap, Excel workbook and charts."""
import numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from data import CFG

def assign_split(T, days):
    n = len(days); a, b = int(n*0.6), int(n*0.8)
    idx = {d: i for i, d in enumerate(days)}
    T = T.copy(); T["split"] = T.date.map(lambda d: "DEV(60%)" if idx[d] < a else ("VALID(20%)" if idx[d] < b else "OOS(20%)"))
    T["month"] = T.date.str[:7]; T["week"] = pd.to_datetime(T.date).dt.strftime("%G-W%V")
    T["tod"] = pd.cut(T.entry_min, [0, 600, 660, 750, 1000], labels=["09:30-10:00", "10:00-11:00", "11:00-12:30", "12:30-15:15"], right=False)
    return T

def metrics(t, n_weeks=None, n_months=None):
    if len(t) == 0: return dict(trades=0)
    t = t.sort_values(["date", "entry_min"]); w = t[t.net > 0]; l = t[t.net <= 0]; eq = t.net.cumsum()
    st = mx = 0
    for v in t.net: st = st+1 if v <= 0 else 0; mx = max(mx, st)
    se = t.net.std(ddof=1)/np.sqrt(len(t)) if len(t) > 1 else np.nan
    return dict(trades=len(t), winners=len(w), losers=len(l), target_hit_rate=round((t.kind == "TARGET").mean()*100, 1),
                sl_rate=round((t.kind == "SL").mean()*100, 1), eod_rate=round((t.kind == "EOD").mean()*100, 1),
                net_win_rate=round(len(w)/len(t)*100, 1), gross_profit=round(w.gross.sum()), gross_loss=round(l.gross.sum()),
                net_pnl=round(t.net.sum()), profit_factor=round(w.net.sum()/abs(l.net.sum()), 2) if len(l) and l.net.sum() else None,
                expectancy=round(t.net.mean(), 1), avg_win=round(w.net.mean()) if len(w) else None, avg_loss=round(l.net.mean()) if len(l) else None,
                t_stat=round(t.net.mean()/se, 2) if se == se and se else None, max_drawdown=round((eq.cummax()-eq).max()),
                max_consec_losses=mx, avg_hold_min=round(t.hold_min.mean()), trades_per_week=round(len(t)/(n_weeks or t.week.nunique()), 2),
                trades_per_month=round(len(t)/(n_months or t.month.nunique()), 1),
                pos_months_pct=round((t.groupby("month").net.sum() > 0).mean()*100))

def boot(t, n=5000, seed=7):
    if len(t) < 5: return dict(n=len(t))
    rng = np.random.default_rng(seed); hit = (t.kind == "TARGET").values.astype(float); net = t.net.values
    ih = rng.integers(0, len(t), (n, len(t)))
    hr, mn = hit[ih].mean(1)*100, net[ih].mean(1)
    byday = t.groupby("date").net.sum().sort_values(ascending=False)
    top5 = byday.head(5).sum(); tot = byday.sum()
    return dict(n=len(t), hit_rate=round(hit.mean()*100, 1), hit_ci_lo=round(np.percentile(hr, 2.5), 1), hit_ci_hi=round(np.percentile(hr, 97.5), 1),
                mean_net=round(net.mean(), 1), mean_ci_lo=round(np.percentile(mn, 2.5), 1), mean_ci_hi=round(np.percentile(mn, 97.5), 1),
                top5_days_net=round(top5), total_net=round(tot), p_mean_gt0=round((mn > 0).mean()*100, 1))

def grp(T, by):
    rows = []
    for k, t in T.groupby(by, observed=True):
        m = metrics(t); m[by if isinstance(by, str) else "group"] = k; rows.append(m)
    return pd.DataFrame(rows)

def summary_table(T):
    rows = []
    for (s, f, sel), t in T.groupby(["strategy", "regime_filter", "sel"]):
        for per in ("ALL", "DEV(60%)", "VALID(20%)", "OOS(20%)"):
            sub = t if per == "ALL" else t[t.split == per]
            m = metrics(sub); m.update(strategy=s, regime_filter=f, sel=sel, period=per); rows.append(m)
    cols = ["strategy", "regime_filter", "sel", "period"]
    df = pd.DataFrame(rows); return df[cols+[c for c in df.columns if c not in cols]]

def charts(T, outdir):
    base = T[(~T.regime_filter) & (T.sel == "ATM")]
    fig, ax = plt.subplots(3, 1, figsize=(12, 13))
    for s, t in base.groupby("strategy"):
        t = t.sort_values(["date", "entry_min"]); eq = t.net.cumsum().values
        ax[0].plot(eq, label=s[:30]); ax[1].plot(eq-np.maximum.accumulate(eq))
    ax[0].set_title("Equity curve, 1 lot, ATM, no regime filter (net Rs)"); ax[0].legend(fontsize=6); ax[1].set_title("Drawdown (Rs)")
    mo = base.pivot_table(index="month", columns="strategy", values="net", aggfunc="sum").fillna(0)
    mo.plot(kind="bar", ax=ax[2], legend=False, width=.9); ax[2].set_title("Monthly net P&L (Rs)")
    plt.tight_layout(); plt.savefig(f"{outdir}/equity_drawdown_monthly.png", dpi=105); plt.close()
    fig, ax = plt.subplots(2, 3, figsize=(16, 9))
    g = base.groupby("strategy"); lab = [s[:3].strip() for s in g.groups]
    g.apply(lambda t: (t.kind == "TARGET").mean()*100).plot(kind="bar", ax=ax[0, 0], title="Target hit rate % (+15 before -10)")
    g.apply(lambda t: t[t.net > 0].net.sum()/max(abs(t[t.net <= 0].net.sum()), 1)).plot(kind="bar", ax=ax[0, 1], title="Profit factor"); ax[0, 1].axhline(1, c="k", lw=.8)
    g.apply(lambda t: (t.sort_values(["date", "entry_min"]).net.cumsum().cummax()-t.sort_values(["date", "entry_min"]).net.cumsum()).max()).plot(kind="bar", ax=ax[0, 2], title="Max drawdown (Rs)")
    base.groupby(["strategy", "dir"]).net.sum().unstack().plot(kind="bar", ax=ax[1, 0], title="CE vs PE net Rs")
    g.apply(lambda t: len(t)/t.week.nunique() if len(t) else 0).plot(kind="bar", ax=ax[1, 1], title="Trades per active week")
    base.groupby(["tod", "strategy"], observed=True).net.sum().unstack().plot(kind="bar", ax=ax[1, 2], legend=False, title="Time-of-day net Rs")
    for a in ax.flat: a.tick_params(axis="x", labelsize=6)
    plt.tight_layout(); plt.savefig(f"{outdir}/comparison_charts.png", dpi=105); plt.close()
