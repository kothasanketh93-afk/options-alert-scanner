"""AI tutor (added 2026-11-12): Claude writes the Telugu explanation inside the setup / result / daily-lesson images, as a
mentor with 30+ years of options trading and teaching. Data collection and learning only -- never places or gates a trade.

* Provider: Claude when the ANTHROPIC_API_KEY secret is set (paid); otherwise Google Gemini when GEMINI_API_KEY is set
  (Google AI Studio free tier -- free-tier prompts may be used by Google to improve its products; nothing personal is
  sent, only chart images and trade numbers). Without either key, or on any API error or timeout, callers fall back to
  the rule-based Telugu text in lessons.py, so the images always go out.
* The model sees the chart picture (vision) plus the exact numbers; it is told to use only those numbers.
* It also gives a one-word tutor view of a setup (supportive / mixed / against). That is stored on the trade
  (trade["ai_verdict"]) and in the ledger, so after a few weeks the data shows whether the tutor's view actually
  separates winners from losers -- it is not used to filter alerts until it has proved that.
"""
import base64
import json
import os

import config
from logging_setup import get_logger

log = get_logger("ai_tutor")
API_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM = (
    "You are a professional options trader with more than 30 years of experience in Indian index, stock and MCX "
    "commodity options, and a patient teacher. Your student is an adult beginner who learns best in Telugu. He trades "
    "intraday option setups from a scanner and wants to understand WHY a setup exists, WHEN it is wrong, and HOW a "
    "professional would manage it, so that over time he picks only high-quality trades.\n"
    "Rules:\n"
    "- Write in simple, natural Telugu (Telugu script). Keep common trading words in English: entry, SL, stop-loss, "
    "target, premium, CE, PE, support, resistance, breakout, trend, candle, volume, OI, theta, spread, lot.\n"
    "- Use ONLY the numbers and facts given in the data and visible on the chart. Never invent prices, news or events.\n"
    "- Be honest and specific. If the setup looks weak, say so plainly and say why. Never promise profit.\n"
    "- Always teach risk: where the idea is wrong, and what one lot can lose.\n"
    "- Short sentences. Each point at most 2 sentences. No greetings, no disclaimers.\n"
    "- Reply with JSON only, no markdown fences."
)

SETUP_TASK = (
    "This is a NEW intraday option setup from the scanner. The chart shows the underlying's 5-minute candles today; the "
    "last candle is the signal. Teach the student this setup. Return JSON:\n"
    '{"sections":[{"title":"...","points":["..."]}], "verdict":"supportive|mixed|against", "verdict_reason":"..."}\n'
    "Sections, in this order, with Telugu titles: (1) what this setup is, in one or two points; (2) what the chart shows "
    "right now (trend, where price is versus the marked levels, the signal candle); (3) quality check: points FOR the "
    "trade and points AGAINST it; (4) how a professional would manage it: when NOT to enter, when to exit early, what "
    "makes the idea wrong (the exact entry/SL/target numbers are printed separately below your text, do not repeat "
    "them all); "
    "(5) one thing a professional would watch in the next 15 minutes. 2-3 points per section. verdict = your honest "
    "teaching view of setup quality; verdict_reason = one Telugu sentence."
)

REVIEW_TASK = (
    "This trade has CLOSED. The chart shows the option premium's 1-minute candles from the alert to the exit, with "
    "entry, SL and targets marked. The data includes rule-based observations already computed from the tracked prices. "
    "Review it like a mentor. Return JSON:\n"
    '{"sections":[{"title":"...","points":["..."]}]}\n'
    "Sections with Telugu titles: (1) what happened, in order; (2) what went right / what went wrong (use the "
    "observations, add what the chart shows); (3) the lesson to carry to the next trade, as one clear rule. "
    "2-3 points per section. Judge the decision, not just the outcome: a well-planned trade can still hit SL."
)

DAY_TASK = (
    "These are today's closed focus trades from the student's scanner (one lot each), with rule-based observations. "
    "Write today's lesson card. Return JSON:\n"
    '{"sections":[{"title":"...","points":["..."]}]}\n'
    "Sections with Telugu titles: (1) today in one look (results, what kind of market day it was, from the data only); "
    "(2) the mistakes or patterns that repeated, each with the fix; (3) what worked; (4) tomorrow's single rule to "
    "follow; (5) homework: two short questions for the student to answer about today's best and worst trade. "
    "2-3 points per section."
)


def provider():
    """'claude', 'gemini' or None."""
    if not getattr(config, "AI_TUTOR_ENABLED", True):
        return None
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


def enabled() -> bool:
    return provider() is not None


def _gemini(task: str, data: dict, image_path, timeout):
    import requests
    parts = []
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as fh:
            parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(fh.read()).decode()}})
    parts.append({"text": task + "\n\nDATA:\n" + json.dumps(data, ensure_ascii=False, default=str)})
    model = getattr(config, "AI_TUTOR_GEMINI_MODEL", "gemini-2.5-flash")
    resp = requests.post(GEMINI_URL.format(model=model), timeout=timeout, json={
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 4096, "temperature": 0.4}},
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "content-type": "application/json"})
    if resp.status_code != 200:
        log.warning("AI_TUTOR gemini HTTP %s: %s", resp.status_code, resp.text[:300])
        return None
    cands = resp.json().get("candidates") or []
    return "".join(p.get("text", "") for p in ((cands[0].get("content") or {}).get("parts") or [])) if cands else None


def _model(kind: str) -> str:
    return getattr(config, "AI_TUTOR_MODEL", "claude-sonnet-5-5")


def _call(task: str, data: dict, image_path: str = None, timeout: float = None):
    """One Messages API call; returns the parsed JSON dict or None (never raises)."""
    prov = provider()
    if not prov:
        return None
    try:
        import requests
        timeout = timeout or getattr(config, "AI_TUTOR_TIMEOUT_S", 60)
        if prov == "gemini":
            text = _gemini(task, data, image_path, timeout) or ""
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                log.warning("AI_TUTOR gemini: no JSON in reply: %s", text[:200])
                return None
            out = json.loads(text[start:end + 1])
            return out if isinstance(out.get("sections"), list) and out["sections"] else None
        content = []
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as fh:
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                            "data": base64.b64encode(fh.read()).decode()}})
        content.append({"type": "text", "text": task + "\n\nDATA:\n" + json.dumps(data, ensure_ascii=False, default=str)})
        resp = requests.post(API_URL, timeout=timeout, json={
            "model": _model(task), "max_tokens": 2500, "system": SYSTEM,
            "messages": [{"role": "user", "content": content}]},
            headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        if resp.status_code != 200:
            log.warning("AI_TUTOR HTTP %s: %s", resp.status_code, resp.text[:300])
            return None
        text = "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            log.warning("AI_TUTOR no JSON in reply: %s", text[:200])
            return None
        out = json.loads(text[start:end + 1])
        return out if isinstance(out.get("sections"), list) and out["sections"] else None
    except Exception as exc:
        log.warning("AI_TUTOR call failed (%s: %s)", type(exc).__name__, exc)
        return None


def to_lines(reply: dict) -> list:
    """AI JSON -> lessons-style [(text, kind)] for the image panel."""
    lines = []
    for sec in reply.get("sections") or []:
        title = str(sec.get("title") or "").strip()
        if title:
            lines.append((title, "head"))
        for p in sec.get("points") or []:
            p = str(p).strip()
            if p:
                lines.append((p, "plain"))
    return lines


_VERDICT_TE = {"supportive": ("Tutor అభిప్రాయం: సానుకూలం ✅", "good"), "mixed": ("Tutor అభిప్రాయం: మిశ్రమం ⚖️", "warn"),
               "against": ("Tutor అభిప్రాయం: వ్యతిరేకం ⛔", "warn")}


def setup_facts(trade: dict, lot=None, pdh=None, pdl=None) -> dict:
    keys = ("symbol", "direction", "strike", "expiry", "strategy", "engine", "alert_time", "spot_price", "alert_premium",
            "stop_loss", "target_1", "target_2", "level_name", "level_value", "sweep_extreme", "fvg", "spread_pct",
            "why_reasons", "entry_oi", "entry_volume", "vix_at_alert", "nifty_chg_pct_at_alert")
    d = {k: trade.get(k) for k in keys if trade.get(k) not in (None, "", [])}
    d.update({"view": "bullish: buy CE" if trade.get("direction") == "CE" else "bearish: buy PE", "lot_size": lot,
              "previous_day_high": pdh, "previous_day_low": pdl})
    return d


def setup_guidance(trade: dict, chart_png: str, lot=None, pdh=None, pdl=None):
    """[(text, kind)] Telugu panel lines from the tutor, or None to fall back. Records trade["ai_verdict"]."""
    reply = _call(SETUP_TASK, setup_facts(trade, lot, pdh, pdl), chart_png)
    if not reply:
        return None
    lines = to_lines(reply)
    v = str(reply.get("verdict") or "").lower()
    if v in _VERDICT_TE:
        trade["ai_verdict"] = v
        text, kind = _VERDICT_TE[v]
        reason = str(reply.get("verdict_reason") or "").strip()
        lines = [(text + (f" – {reason}" if reason else ""), kind)] + lines
    return lines


def review_guidance(trade: dict, chart_png: str, lot=None, observations=None):
    import outcomes_ledger
    keys = ("symbol", "direction", "strike", "strategy", "engine", "alert_time", "alert_premium", "tracked_entry_price",
            "actual_entry_time", "stop_loss", "initial_stop_loss", "target_1", "target_2", "final_status", "t1_hit",
            "max_favorable_pct", "max_adverse_pct", "spread_pct", "expiry", "eod_last_price", "ai_verdict")
    d = {k: trade.get(k) for k in keys if trade.get(k) not in (None, "", [])}
    d.update({"result_pct_on_premium": outcomes_ledger.result_pct(trade), "lot_size": lot,
              "observations": observations or [], "sell_view": trade.get("sell_view")})
    reply = _call(REVIEW_TASK, d, chart_png)
    return to_lines(reply) if reply else None


def day_guidance(summary: dict):
    reply = _call(DAY_TASK, summary)
    return to_lines(reply) if reply else None
