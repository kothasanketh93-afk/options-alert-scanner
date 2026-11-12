"""Check the AI tutor end to end (manual / one-off workflow): which provider is configured, one real call with a sample
chart, and the resulting Telugu setup chart sent to Telegram. Prints the outcome; never prints the API key."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_tutor
import telegram_notify
import trade_chart


def sample():
    random.seed(7)
    cs, p = [], 24000.0
    for i in range(46):
        h, m = divmod(9 * 60 + 15 + 5 * i, 60)
        o, p = p, p + random.gauss(0, 18) - 3
        cs.append({"timestamp": f"2026-11-12T{h:02d}:{m:02d}:00+05:30", "open": o, "high": max(o, p) + 6,
                   "low": min(o, p) - 6, "close": p})
    sig = cs[-1]
    t = {"trade_id": "verify", "symbol": "NIFTY", "direction": "CE", "strike": 24000.0, "strategy": "fvg",
         "engine": "sweep_reversal", "alert_time": sig["timestamp"], "spot_price": round(sig["close"], 2),
         "level_name": "PDL", "level_value": round(min(c["low"] for c in cs[30:]), 2), "alert_premium": 120.5,
         "stop_loss": 96.0, "target_1": 150.0, "target_2": 180.0, "fvg": (round(sig["close"] - 20, 2), round(sig["close"] - 8, 2))}
    return t, cs


def main():
    prov = ai_tutor.provider()
    print(f"AI tutor provider: {prov or 'NONE (no ANTHROPIC_API_KEY / GEMINI_API_KEY secret visible)'}")
    import time
    t0 = time.time()
    t, cs = sample()
    path = trade_chart.temp_png("verify_ai_tutor")
    ok = trade_chart.setup_chart(t, cs, path, strategy="VERIFY / sample data", lot=75)
    status = (f"AI tutor test: provider={prov}, AI text used={'YES' if 'ai_verdict' in t else 'NO (fell back to rule-based)'}"
              if prov else "AI tutor test: no AI key found; the chart below uses rule-based Telugu text")
    print(status, f"({time.time() - t0:.0f}s)")
    if ok:
        telegram_notify.send_photo(path, caption=status + " (sample data, not a trade)")
    return 0 if (prov and "ai_verdict" in t) else 1


if __name__ == "__main__":
    sys.exit(main())
