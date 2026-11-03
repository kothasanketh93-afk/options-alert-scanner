"""End-of-day trade report (Telegram): every tracked trade today (live + shadow engines) grouped by outcome.

Educational/data tool, not investment advice. Pure formatting from state; sending is done by main.py once per day.
"""
from html import escape

import outcomes_ledger

_ENGINES = (("trades", "LIVE"), ("shadow_trades", "Main shadow"), ("fib_pivot_shadow_trades", "Fib pivot"),
            ("orb_pivot_shadow_trades", "ORB"))
_GROUPS = (
    ("win", "✅ Targets hit (T2)", ("T2_HIT",)),
    ("partial", "🟡 T1 hit, then stopped", ("T1_THEN_SL", "T1_THEN_TRAILING_SL", "T1_HIT_THEN_EOD", "T1_HIT")),
    ("loss", "❌ Stop-loss hit", ("SL_HIT", "TRAILING_SL_HIT")),
    ("other", "⏳ Open / closed at end of day / not entered", None),
)


def _fmt(x):
    return "-" if x is None else (f"{x:.2f}" if isinstance(x, (int, float)) else str(x))


def collect(state: dict) -> list:
    rows = []
    for key, label in _ENGINES:
        for t in (state.get(key) or {}).values():
            status = t.get("final_status") or t.get("status")
            res = outcomes_ledger.result_pct(t)
            rows.append({"engine": label, "t": t, "status": status, "net": outcomes_ledger.net_result_pct(res)})
    return rows


def _group_of(status):
    for gid, _title, members in _GROUPS:
        if members and status in members:
            return gid
    return "other"


def _line(r) -> str:
    t = r["t"]
    when = (t.get("alert_time") or "")[11:16]
    net = f" | net {r['net']:+.1f}%" if r["net"] is not None else ""
    return (f"• {escape(str(t.get('symbol')))} {escape(str(t.get('direction')))} {escape(str(t.get('strike')))} "
            f"[{r['engine']}, {when}] entry {_fmt(t.get('tracked_entry_price') or t.get('alert_premium'))}, "
            f"SL {_fmt(t.get('stop_loss'))}, T1 {_fmt(t.get('target_1'))}, T2 {_fmt(t.get('target_2'))} "
            f"→ {escape(str(r['status']))}{net}")


def build_messages(state: dict, date_str: str, limit: int = 3800) -> list:
    rows = collect(state)
    if not rows:
        return []
    by = {g[0]: [r for r in rows if _group_of(r["status"]) == g[0]] for g in _GROUPS}
    head = [f"📊 <b>End-of-day trade report {escape(date_str)}</b>",
            f"Tracked: {len(rows)} | T2: {len(by['win'])} | T1 then SL: {len(by['partial'])} | "
            f"SL: {len(by['loss'])} | open/other: {len(by['other'])}"]
    engines = []
    for _k, label in _ENGINES:
        er = [r for r in rows if r["engine"] == label]
        if er:
            engines.append(f"{label}: {sum(1 for r in er if _group_of(r['status']) == 'win')} T2, "
                           f"{sum(1 for r in er if _group_of(r['status']) == 'partial')} T1→SL, "
                           f"{sum(1 for r in er if _group_of(r['status']) == 'loss')} SL of {len(er)}")
    head.append("By engine – " + "; ".join(engines))
    lines = ["\n".join(head)]
    for gid, title, _m in _GROUPS:
        if by[gid]:
            lines.append(f"\n<b>{title}</b>")
            lines += [_line(r) for r in by[gid]]
    lines.append("\nNet % assumes round-trip costs. Shadow = data collection, not signals. "
                 "Educational only; not a SEBI-registered advisor.")
    msgs, cur = [], ""
    for ln in lines:
        if cur and len(cur) + len(ln) + 1 > limit:
            msgs.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + ln
    if cur:
        msgs.append(cur)
    return msgs
