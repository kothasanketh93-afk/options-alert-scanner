from datetime import datetime

import shadow_notify as sn

NOW = datetime(2026, 11, 2, 10, 0)


def _trade(**kw):
    t = {"symbol": "NIFTY", "direction": "PE", "strike": 25000, "moneyness": "ATM", "strategy": "orb15", "engine": "orb_pivot",
         "alert_premium": 100.0, "stop_loss": 90.0, "target_1": 110.0, "target_2": 120.0,
         "trade_score": 62, "grade": "B", "why_reasons": ["a", "b"], "tracked_entry_price": 101.0}
    t.update(kw)
    return t


def test_created_once_per_group_and_flags_trade():
    sent, st = [], {}
    a, b = _trade(moneyness="ITM"), _trade(moneyness="OTM")
    assert sn.notify_created(st, a, NOW, sender=sent.append) is True
    assert sn.notify_created(st, b, NOW, sender=sent.append) is False
    assert len(sent) == 1 and "NOT a trade signal" in sent[0] and a["shadow_notified"] and "shadow_notified" not in b


def test_new_day_resets_dedupe_and_prunes_old_keys():
    sent, st = [], {}
    sn.notify_created(st, _trade(), NOW, sender=sent.append)
    assert sn.notify_created(st, _trade(), datetime(2026, 11, 3, 10, 0), sender=sent.append)
    assert all(k.startswith("2026-11-03") for k in st["shadow_notified_keys"])


def test_send_failure_is_swallowed_and_not_marked():
    def boom(_):
        raise RuntimeError("telegram down")
    t, st = _trade(), {}
    assert sn.notify_created(st, t, NOW, sender=boom) is False
    assert not t.get("shadow_notified") and st["shadow_notified_keys"] == []


def test_outcomes_only_for_flagged_trades():
    sent = []
    ev = [{"type": "ENTRY_ACTIVATED"}, {"type": "SL_HIT"}]
    assert sn.notify_events(_trade(), ev, sender=sent.append) == 0
    assert sn.notify_events(_trade(shadow_notified=True), ev, sender=sent.append) == 1
    assert "Stop-loss hit" in sent[0]


def test_disabled_flag(monkeypatch):
    monkeypatch.setattr(sn.config, "SHADOW_TELEGRAM_ENABLED", False)
    assert sn.notify_created({}, _trade(), NOW, sender=lambda _: 1 / 0) is False


def test_main_engine_shadow_suppressed_in_conditions_mode(monkeypatch):
    monkeypatch.setattr(sn.config, "CONDITIONS_ALERT_MODE", True)
    t = _trade(); t.pop("engine")
    assert sn.notify_created({}, t, NOW, sender=lambda _: 1 / 0) is False
