import os, sys
_here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(_here))
import eod_report as er


def _t(sym, status, entry=10.0, sl=8.0, t2=15.0):
    return {"symbol": sym, "direction": "CE", "strike": 100, "alert_time": "2026-11-03T09:31:00+05:30",
            "tracked_entry_price": entry, "stop_loss": sl, "target_1": 12.0, "target_2": t2, "status": status, "final_status": status}


def test_groups_counts_and_net():
    st = {"trades": {"a": _t("A", "T1_THEN_SL")},
          "shadow_trades": {"b": _t("B", "T2_HIT"), "c": _t("C", "SL_HIT"), "d": _t("D", "ACTIVE")},
          "fib_pivot_shadow_trades": {"e": _t("E", "SL_HIT")}}
    msgs = er.build_messages(st, "2026-11-03")
    txt = "\n".join(msgs)
    assert "Tracked: 5 | T2: 1 | T1 then SL: 1 | SL: 2 | open/other: 1" in txt
    assert "B CE 100 [Main shadow, 09:31]" in txt and "net +48.5%" in txt     # (15-10)/10=50% - 1.5 cost
    assert "net -21.5%" in txt                                                  # (8-10)/10=-20% - 1.5
    assert txt.index("Targets hit") < txt.index("T1 hit, then stopped") < txt.index("Stop-loss hit")


def test_empty_and_split():
    assert er.build_messages({}, "d") == []
    st = {"trades": {str(i): _t("S%d" % i, "SL_HIT") for i in range(80)}}
    msgs = er.build_messages(st, "d", limit=1500)
    assert len(msgs) > 1 and all(len(m) <= 1700 for m in msgs)


def test_sent_once():
    import main as m
    from datetime import datetime
    sent = []
    m.tg.send = sent.append
    st = {"date": "2026-11-03", "trades": {"a": _t("A", "SL_HIT")}}
    m.maybe_send_eod_report(st, datetime(2026, 11, 3, 10, 0)); assert not sent      # before SUMMARY_HHMM
    m.maybe_send_eod_report(st, datetime(2026, 11, 3, 15, 31)); n = len(sent); assert n >= 1
    m.maybe_send_eod_report(st, datetime(2026, 11, 3, 15, 35)); assert len(sent) == n
