import json

import day_review as D


def _log(tmp_path, lines):
    p = tmp_path / "scan.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    return p


def test_summarize_counts_reasons_once_per_option(tmp_path):
    p = _log(tmp_path, [
        {"tag": "REJECTED", "message": "REJECTED NIFTY CE ATM strike=25300.0 | Reasons: Poor Risk Reward, Weak Trend, No OI Build-up"},
        {"tag": "REJECTED", "message": "REJECTED BANKNIFTY PE OTM strike=55000.0 | Reasons: Poor Risk Reward, Low Volume"},
        {"tag": "GATE", "message": "GATE NIFTY CE ATM strike=1 hard_gate=FAIL | x"},
        {"tag": "GATE", "message": "GATE NIFTY CE ATM strike=1 hard_gate=PASS | x"},
        {"tag": "OTHER", "message": "ignored"}, ])
    s = D.summarize(p)
    assert s["rejected"] == 2 and s["symbols"] == 2 and s["gate_pass"] == 1 and s["gate_fail"] == 1
    assert s["reasons"]["risk_reward"] == 2 and s["reasons"]["trend"] == 1 and s["reasons"]["volume"] == 1


def test_missing_or_garbled_log_is_safe(tmp_path):
    assert D.summarize(tmp_path / "nope.jsonl") == {}
    p = tmp_path / "bad.jsonl"
    p.write_text("not json\n\n{}\n")
    assert D.summarize(p)["rejected"] == 0


def test_no_trade_day_never_invents_trades():
    txt = D.build_text({"date": "2026-11-12", "trades": {}}, {}, "2026-11-12")
    assert "live alert లేదు" in txt and "hypothetical" in txt and "Live alerts:" not in txt


def test_trade_day_reports_only_tracked_outcomes():
    st = {"date": "2026-11-12", "trades": {"a": {"status": "T2_HIT"}, "b": {"status": "SL_HIT"}, "c": {"status": "ACTIVE"}}}
    txt = D.build_text(st, {}, "2026-11-12")
    assert "Live alerts: 3 (గెలుపు 1, SL 1)" in txt


def test_practice_rotation_is_deterministic_by_date():
    a = D.build_text({"trades": {}}, {}, "2026-11-12")
    assert a == D.build_text({"trades": {}}, {}, "2026-11-12")
    assert a != D.build_text({"trades": {}}, {}, "2026-11-13")
