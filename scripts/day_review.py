"""Post-market "why was there (or was there no) trade today" review, in Telugu (added 2026-11-12).

Facts only: counts come from today's scan log (REJECTED / GATE lines the scanner already writes) and from the tracked trades.
Nothing is invented: no trade examples when there were no trades, no hindsight claims. The practice exercise is labelled as a
practice exercise (hypothetical), not a market fact.
"""
import json
import re
from collections import Counter
from datetime import date

# keyword in the scanner's rejection text -> (key, Telugu label). Every rejected option counts once per key.
_CATS = [
    ("Poor Risk Reward", "risk_reward", "Risk:Reward బలహీనం (లాభం కంటే నష్టం ఎక్కువ)"),
    ("No OI Build-up", "oi", "OI build-up లేదు (పెద్ద players ఇంకా ప్రవేశించలేదు)"),
    ("Weak Trend", "trend", "Trend బలహీనం"),
    ("Low Liquidity", "liquidity", "Liquidity తక్కువ (spread వెడల్పు)"),
    ("Low Volume", "volume", "Volume తక్కువ"),
    ("Against VWAP", "vwap", "VWAP కి వ్యతిరేక దిశ"),
    ("Premium Outside", "premium", "Premium పరిధి బయట"),
    ("Direction confirmations", "direction", "దిశ నిర్ధారణ సరిపోలేదు (EMA / Trend / Momentum / VWAP)"),
    ("ATR", "extended", "ధర ఇప్పటికే ఎక్కువ కదిలింది (chase చేయకూడదు)"),
]
_REJ = re.compile(r"REJECTED (\S+) (CE|PE) (\S+) strike=\S+ \| Reasons: (.*)")

PRACTICE = [
    "NIFTY 5-నిమిషాల chart తెరిచి, ఈ రోజు మొదటి 15 నిమిషాల high మరియు low గుర్తించండి (opening range). ఏ దిశలో దాటింది?",
    "ఈ రోజు chart లో VWAP కి పైన ఎంత సేపు ఉంది, కింద ఎంత సేపు? ధర VWAP చుట్టూ తిరిగితే trade చేయకపోవడం ఎందుకు మంచిది?",
    "నిన్నటి high (PDH) మరియు low (PDL) గీయండి. ఈ రోజు ధర వాటిని తాకిందా? తాకినప్పుడు ఏ candle వచ్చింది?",
    "ఒక CE option chart లో 3 వరుస candles చూసి, ప్రతి candle లో కొనేవారు గెలిచారా అమ్మేవారా చెప్పండి (body పెద్దదా, wick పెద్దదా).",
    "Premium, SL, Target తో ఒక hypothetical trade రాయండి (ఇది అభ్యాసం మాత్రమే): risk ఎంత రూపాయలు, reward ఎంత, R:R ఎంత?",
    "ఈ రోజు scanner reject చేసిన ప్రధాన కారణం ఏమిటో పైన చూడండి. ఆ కారణం chart లో మీకు కనిపిస్తోందా?",
    "ఈ రోజు EMA 9 మరియు EMA 21 ఎన్నిసార్లు cross అయ్యాయి? ఎక్కువసార్లు cross అయితే అది trending market ఆ, sideways ఆ?",
]


def summarize(scan_path):
    """{'rejected': n, 'symbols': n, 'gate_pass': n, 'gate_fail': n, 'reasons': Counter} from today's scan log ({} if absent)."""
    out = {"rejected": 0, "symbols": set(), "gate_pass": 0, "gate_fail": 0, "reasons": Counter()}
    try:
        with open(scan_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                tag, msg = r.get("tag"), r.get("message") or ""
                if tag == "REJECTED":
                    m = _REJ.match(msg)
                    if not m:
                        continue
                    out["rejected"] += 1
                    out["symbols"].add(m.group(1))
                    for kw, key, _ in _CATS:
                        if kw in m.group(4):
                            out["reasons"][key] += 1
                elif tag == "GATE":
                    out["gate_pass" if "hard_gate=PASS" in msg else "gate_fail"] += 1
    except OSError:
        return {}
    out["symbols"] = len(out["symbols"])
    return out


def _outcomes(state):
    trades = list((state.get("trades") or {}).values())
    wins = sum(1 for t in trades if (t.get("final_status") or t.get("status")) in ("T2_HIT", "T1_HIT_THEN_EOD"))
    losses = sum(1 for t in trades if (t.get("final_status") or t.get("status")) in ("SL_HIT", "T1_THEN_SL"))
    return len(trades), wins, losses


def build_text(state, summary, day=None):
    day = day or state.get("date") or date.today().isoformat()
    n, wins, losses = _outcomes(state)
    labels = {k: lab for _, k, lab in _CATS}
    lines = [f"📘 ఈ రోజు సమీక్ష {day}"]
    if summary:
        lines.append(f"Scanner {summary['symbols']} symbols లో {summary['rejected']} option అవకాశాలను పరిశీలించి reject చేసింది; "
                     f"{summary['gate_pass']} సార్లు hard gate దాటింది, {summary['gate_fail']} సార్లు విఫలమైంది.")
        top = summary["reasons"].most_common(4)
        if top:
            lines.append("ప్రధాన కారణాలు (ఎన్నిసార్లు):")
            lines += [f"• {labels[k]}: {c}" for k, c in top]
    if n == 0:
        lines.append("\nఈ రోజు live alert లేదు. ట్రేడ్ చేయకపోవడం కూడా ఒక నిర్ణయమే: పై కారణాలలో ఏదైనా ఉన్నప్పుడు అవకాశం బలహీనం, "
                     "అలాంటప్పుడు వేచి ఉండటం capital ను కాపాడుతుంది.")
    else:
        lines.append(f"\nLive alerts: {n} (గెలుపు {wins}, SL {losses}). ప్రతి trade ఫలితం కంటే process ముఖ్యం: entry confirmation, SL, "
                     "target ప్రణాళిక ప్రకారం జరిగిందా అని చూడండి.")
    idx = date.fromisoformat(day).toordinal() % len(PRACTICE)
    lines.append(f"\n✍️ అభ్యాసం (hypothetical practice, market fact కాదు):\n{PRACTICE[idx]}")
    lines.append("\nఇది విద్యా సహాయం మాత్రమే; సంఖ్యలు scanner log నుండి వచ్చినవి.")
    return "\n".join(lines)
