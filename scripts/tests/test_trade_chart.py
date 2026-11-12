import os, sys, tempfile
_here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(_here))
import trade_chart as tc
import chart_notify
import shadow_notify


def _candles(n=40, start=100.0, step=0.5, minute=5, h0=9, m0=15):
    out, p = [], start
    for i in range(n):
        h, m = divmod(h0 * 60 + m0 + minute * i, 60)
        o, p = p, p + (step if i % 4 else -2 * step)
        out.append({"timestamp": f"2026-11-11T{h:02d}:{m:02d}:00+05:30", "open": o, "high": max(o, p) + 0.3,
                    "low": min(o, p) - 0.3, "close": p})
    return out


def _trade(**kw):
    t = {"trade_id": "T1", "symbol": "NIFTY", "direction": "CE", "strike": 24000.0, "alert_time": "2026-11-11T10:00:00+05:30",
         "spot_price": 105.0, "alert_premium": 50.0, "stop_loss": 40.0, "target_1": 60.0, "target_2": 70.0,
         "level_name": "PDL", "level_value": 101.0, "sweep_extreme": 100.5, "fvg": (103.0, 104.0), "why_reasons": ["x"]}
    t.update(kw)
    return t


def test_swing_levels_and_patterns_are_bounded():
    cs = _candles(60)
    lv = tc.swing_levels(cs)
    assert 0 < len(lv) <= 4 and all(isinstance(p, float) for p, _ in lv)
    marks = tc.pattern_marks(cs)
    assert len(marks) <= 5 and all(b in ("bullish", "bearish") for _i, _n, b in marks)
    assert tc.swing_levels(cs[:3]) == []


def test_setup_and_outcome_charts_render():
    with tempfile.TemporaryDirectory() as d:
        assert tc.setup_chart(_trade(), _candles(), os.path.join(d, "s.png"), strategy="Sweep", pdh=130.0, pdl=101.0, lot=75)
        t = _trade(tracked_entry_price=50.0, actual_entry_time="2026-11-11T10:01:00+05:30", status="T2_HIT",
                   final_status="T2_HIT", t2_hit_time="2026-11-11T10:30:00+05:30", max_favorable_pct=40, max_adverse_pct=-5)
        assert tc.outcome_chart(t, _candles(50, 48.0, 0.6, 1, 9, 55), os.path.join(d, "o.png"), lot=75)
        assert not tc.setup_chart(_trade(), [], os.path.join(d, "x.png"))     # no candles -> no image, no error


def test_chart_notify_once_and_never_raises(monkeypatch):
    import market_data, option_chain
    monkeypatch.setattr(option_chain, "resolve_underlying_key", lambda s: "NSE_INDEX|Nifty 50")
    monkeypatch.setattr(market_data, "get_recent_underlying_candles", lambda k, i, n: _candles())
    monkeypatch.setattr(market_data, "get_recent_daily_candles", lambda k, n: [{"timestamp": "2026-11-10T00:00:00+05:30", "high": 130, "low": 101}])
    sent = []
    t = _trade()
    assert chart_notify.setup(t, sender=lambda p, c: sent.append((p, c)))
    assert not chart_notify.setup(t, sender=lambda p, c: sent.append((p, c)))       # once per trade
    assert len(sent) == 1 and os.path.exists(sent[0][0])
    monkeypatch.setattr(market_data, "get_1min_candles_since", lambda k, s: (_ for _ in ()).throw(RuntimeError("down")))
    closed = _trade(tracked_entry_price=50.0, status="SL_HIT", final_status="SL_HIT", sl_hit_time="2026-11-11T10:20:00+05:30",
                    instrument_key="NSE_FO|1")
    assert chart_notify.outcome(closed, sender=lambda p, c: sent.append(p)) is False    # fetch failure swallowed
    assert not chart_notify.outcome(_trade(status="ACTIVE"), sender=lambda p, c: sent.append(p))


def test_shadow_hooks_fire_only_for_real_sends(monkeypatch):
    calls = []
    monkeypatch.setattr(shadow_notify, "SETUP_CHART", lambda t: calls.append("setup"))
    monkeypatch.setattr(shadow_notify, "OUTCOME_CHART", lambda t: calls.append("outcome"))
    monkeypatch.setattr(shadow_notify.config, "SHADOW_TELEGRAM_ENABLED", True, raising=False)
    monkeypatch.setattr(shadow_notify, "_send", lambda text: None)
    from datetime import datetime
    t = _trade(engine="sweep_reversal", strategy="fvg")
    assert shadow_notify.notify_created({}, t, datetime(2026, 11, 11, 10, 0))
    shadow_notify.notify_events(t, [{"type": "T1_HIT"}])
    shadow_notify.notify_events(t, [{"type": "SL_HIT"}])
    assert calls == ["setup", "outcome"]


def test_focus_filter_and_lessons_in_telugu(monkeypatch):
    import config, lessons
    monkeypatch.setattr(config, "FOCUS_STRATEGIES", ("trade_score", "sweep_reversal:fvg", "mcx:ema_pb"))
    assert chart_notify.is_focus({"strategy": "trade_score"}) and chart_notify.is_focus({"engine": "mcx", "strategy": "ema_pb"})
    assert not chart_notify.is_focus({"engine": "fib_pivot", "strategy": "trade_score"})
    assert not chart_notify.setup(_trade(engine="orb_pivot", strategy="orb15"), sender=lambda p, c: None)
    monkeypatch.setattr(config, "FOCUS_ONLY_NOTICES", True)
    assert not shadow_notify.notify_created({}, _trade(engine="orb_pivot", strategy="orb15"), __import__("datetime").datetime(2026, 11, 11),
                                            sender=lambda m: None)
    lines = lessons.setup_lines(_trade(strategy="fvg"), lot=75)
    assert any("Fair Value Gap" in t for t, _k in lines) and any("1 lot" in t for t, _k in lines)
    closed = _trade(tracked_entry_price=50.0, status="SL_HIT", final_status="T1_THEN_SL", t1_hit=True,
                    actual_entry_time="2026-11-11T10:20:00+05:30", sl_hit_time="2026-11-11T10:25:00+05:30",
                    alert_premium=50.0, spread_pct=3.0, max_favorable_pct=25, max_adverse_pct=-20)
    ids = lessons.review_lines(closed, ids_only=True)
    assert {"gave_back", "late_entry", "spread"} <= set(ids)
    assert lessons.day_lessons([closed, closed])[0][0] == 2


def test_lesson_card_sends_text_then_best_and_worst(monkeypatch):
    import config
    monkeypatch.setattr(config, "FOCUS_STRATEGIES", ("trade_score",))
    win = _trade(trade_id="w", strategy="trade_score", tracked_entry_price=50.0, status="T2_HIT", final_status="T2_HIT",
                 actual_entry_time="2026-11-11T10:01:00+05:30", t2_hit_time="2026-11-11T10:30:00+05:30")
    loss = _trade(trade_id="l", strategy="trade_score", tracked_entry_price=50.0, status="SL_HIT", final_status="SL_HIT",
                  actual_entry_time="2026-11-11T10:01:00+05:30", sl_hit_time="2026-11-11T10:20:00+05:30")
    texts, photos = [], []
    ok = chart_notify.lesson_card([win, loss], "2026-11-11", "NSE", lambda t: _candles(50, 48.0, 0.6, 1, 9, 55),
                                  texts.append, lambda p, c: photos.append(c), lot_fn=lambda t: 75)
    assert ok and "Daily lesson" in texts[0] and "1 won" in texts[0]
    assert not any("\u0c00" <= ch <= "\u0c7f" for ch in texts[0] + "".join(photos))   # Telugu only inside images
    assert len(photos) == 3 and photos[0].startswith("Lesson card") and "Best" in photos[1] and "Worst" in photos[2]


def test_ai_tutor_used_when_key_present_and_falls_back(monkeypatch, tmp_path):
    import ai_tutor, json
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    calls = []

    class R:
        status_code = 200

        def __init__(self, body):
            self._b = body
            self.text = ""

        def json(self):
            return self._b

    def fake_post(url, timeout, json=None, headers=None):
        calls.append(json)
        body = {"sections": [{"title": "ఈ setup ఏమిటి?", "points": ["FVG అంటే ఖాళీ zone."]}], "verdict": "mixed",
                "verdict_reason": "trend బలహీనం"}
        return R({"content": [{"type": "text", "text": __import__("json").dumps(body, ensure_ascii=False)}]})
    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    t = _trade(strategy="fvg")
    assert tc.setup_chart(t, _candles(), str(tmp_path / "s.png"), lot=75)
    assert t["ai_verdict"] == "mixed" and calls and calls[0]["model"] == "claude-sonnet-5-5"
    assert calls[0]["messages"][0]["content"][0]["type"] == "image"            # the tutor sees the chart
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
    t2 = _trade(strategy="fvg")
    assert tc.setup_chart(t2, _candles(), str(tmp_path / "s2.png"), lot=75) and "ai_verdict" not in t2   # rule fallback
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert not ai_tutor.enabled()


def test_gemini_free_provider_when_only_gemini_key(monkeypatch, tmp_path):
    import ai_tutor, json as _json, requests
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert ai_tutor.provider() == "gemini"
    seen = []

    class R:
        status_code = 200
        text = ""

        def json(self):
            body = {"sections": [{"title": "ఫలితం", "points": ["SL తగిలింది."]}]}
            return {"candidates": [{"content": {"parts": [{"text": _json.dumps(body, ensure_ascii=False)}]}}]}

    def fake_post(url, timeout, json=None, headers=None):
        seen.append((url, headers, json))
        return R()
    monkeypatch.setattr(requests, "post", fake_post)
    out = ai_tutor.review_guidance(_trade(final_status="SL_HIT"), None)
    assert out and out[0] == ("ఫలితం", "head")
    url, headers, body = seen[0]
    assert "generativelanguage.googleapis.com" in url and headers["x-goog-api-key"] == "g"
    assert body["generationConfig"]["responseMimeType"] == "application/json"


def test_emoji_removed_from_image_text():
    import te_text
    assert te_text.clean("Tutor అభిప్రాయం: మిశ్రమం ⚖️ – ok ✅ ⛔ 🟢") .strip() == "Tutor అభిప్రాయం: మిశ్రమం – ok"
    assert "→" in te_text.clean("a → b")
