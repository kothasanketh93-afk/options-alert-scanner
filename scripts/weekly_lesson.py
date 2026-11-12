"""Weekly concept lesson (added 2026-11-12): every Monday one trading concept, taught in Telugu as an image, with
examples from the student's own trades of the past week. A 12-week course that then repeats.

Written by the AI tutor (ai_tutor.py) when a key is set, else the built-in Telugu text below. Telegram caption in English.
Run by main.py on Monday mornings; can also be sent now:  python scripts/weekly_lesson.py <state-branch dir> [week]
"""
import csv
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

COURSE_START = date(2026, 11, 9)       # week 1 = the week of 9 Nov 2026

# key, Telugu title, what the tutor must teach (English, for the model), built-in Telugu points, example picker
COURSE = [
    ("delta", "Delta: underlying కదిలితే premium ఎంత కదులుతుంది",
     "Option delta: how much the premium moves for a 1-point move in the underlying; ATM ~0.5, ITM higher, OTM lower; "
     "why the same Nifty move gives different premium moves; how the scanner turns option targets into underlying levels.",
     ["Delta అంటే: underlying 1 point కదిలితే option premium సుమారు ఎంత కదులుతుందో చెప్పే సంఖ్య.",
      "ATM option delta సుమారు 0.5. అంటే Nifty 100 points పెరిగితే ATM CE premium సుమారు 50 పెరుగుతుంది.",
      "ITM options delta ఎక్కువ (0.6-0.8), ఎక్కువ కదులుతాయి కానీ ఖరీదు ఎక్కువ. OTM delta తక్కువ (0.2-0.4), చౌక కానీ నెమ్మదిగా కదులుతాయి.",
      "మన setup chart లో 'T1 ≈' గీతలు ఇదే లెక్కతో వస్తాయి: (target premium - entry premium) / delta.",
      "నియమం: beginner గా ATM లేదా కొంచెం ITM options నే వాడండి. Deep OTM 'చౌక' options లాటరీ టికెట్ల లాంటివి."], "best_worst"),
    ("theta", "Theta: సమయం కూడా ఒక ఖర్చు",
     "Theta / time decay: an option buyer loses premium every minute the underlying does not move; faster near expiry "
     "and in the afternoon; why sideways days hurt buyers and help sellers.",
     ["Theta అంటే: ఏమీ జరగకపోయినా ప్రతి నిమిషం premium కరిగిపోవడం (time decay).",
      "Expiry దగ్గర పడేకొద్దీ theta వేగంగా పెరుగుతుంది. Expiry రోజు మధ్యాహ్నం అత్యంత వేగం.",
      "ధర పక్కకి (sideways) కదిలే రోజు option buyer కి నష్టం, seller కి లాభం.",
      "నియమం: buy trade తర్వాత 45-60 నిమిషాల్లో ధర కదలకపోతే, SL కోసం ఎదురుచూడకుండా బయటకు రావడం మంచిది."], "theta"),
    ("levels", "నిన్నటి high / low: రోజంతా ముఖ్యమైన స్థాయిలు",
     "Previous-day high and low (PDH/PDL) and the opening range: why big players watch them, breakout vs fake breakout, "
     "how to mark them every morning.",
     ["ప్రతి ఉదయం chart మీద నిన్నటి high (PDH), నిన్నటి low (PDL) గీతలు గీయండి.",
      "ధర PDH పైన candle close అయితే బలం (breakout). పైకి వెళ్ళి వెంటనే కిందకి వస్తే fake breakout (sweep).",
      "మొదటి 15 నిమిషాల high/low (opening range) కూడా అలాంటి ముఖ్య స్థాయే.",
      "నియమం: ఈ స్థాయిల మధ్యలో (మధ్య ప్రాంతంలో) కొత్త trades తీసుకోవద్దు. స్థాయిల దగ్గరే అవకాశాలు."], "levels"),
    ("trend", "Trend మరియు support/resistance",
     "Trend (higher highs/lows, EMA slope), support and resistance from swing points, trading with the trend vs "
     "counter-trend, reading the orange lines on the scanner charts.",
     ["Uptrend = higher highs, higher lows. Downtrend = lower highs, lower lows.",
      "Support అంటే ధర గతంలో ఆగి పైకి తిరిగిన చోటు. Resistance అంటే ఆగి కిందకి తిరిగిన చోటు. Charts లోని నారింజ గీతలు ఇవే.",
      "Trend దిశలో trades గెలిచే అవకాశం ఎక్కువ. Counter-trend trades కి చిన్న target, కఠినమైన SL.",
      "నియమం: CE కొనే ముందు 'ఇది uptrend నా?' అని, PE కొనే ముందు 'ఇది downtrend నా?' అని అడగండి."], "best_worst"),
    ("risk", "Risk management: ఒక trade కి ఎంత risk",
     "Position sizing: risk at most 1-2% of trading capital per trade; daily loss limit; computing the number of lots "
     "from the stop distance; why survival comes first.",
     ["ఒక trade లో capital లో 1-2% కంటే ఎక్కువ పోకూడదు. ఉదా: Rs 1,00,000 capital అయితే ఒక trade risk గరిష్టం Rs 1,000-2,000.",
      "Risk = (entry - SL) x lot size. ఇది limit కంటే ఎక్కువ ఉంటే ఆ trade వదిలేయండి, లేదా దగ్గర SL ఉన్న setup కోసం ఎదురుచూడండి.",
      "రోజువారీ loss limit: capital లో 3%. అది తాకితే ఆ రోజు trading ఆపేయండి.",
      "నియమం: వరుసగా 3 నష్టాలు వస్తే ఆ రోజు ఆపేయండి. కోపంతో చేసే trades (revenge trading) అతి పెద్ద శత్రువు."], "losses"),
    ("exits", "Risk:Reward మరియు exit నియమాలు",
     "Risk:reward, booking partial profit at T1, moving the stop to entry, trailing; why a 40% win rate can still make "
     "money with good R:R; the scanner's what-if exit rule results.",
     ["Risk:Reward 1:2 అంటే Rs 1 risk కి Rs 2 reward. ఇలా ఉంటే 10 లో 4 గెలిచినా లాభమే.",
      "T1 దగ్గర సగం book చేయండి, మిగతా దానికి SL ను entry కి మార్చండి. అప్పుడు ఆ trade నష్టంగా మారదు.",
      "Weekly report లోని 'What if' boxes ఏ exit rule మీకు మేలో చూపిస్తాయి.",
      "నియమం: entry కి ముందే SL, T1, T2 రాసుకోండి. Trade మధ్యలో SL ను దూరం జరపకండి."], "gave_back"),
    ("fvg", "Fair Value Gap (FVG)",
     "Fair value gap: three-candle imbalance, why price often returns to fill it, entering on the return in the trend "
     "direction, invalidation when price closes through the gap.",
     ["మూడు candles లో, మొదటి candle high కి మూడవ candle low కి మధ్య ఖాళీ ఉంటే అది bullish FVG.",
      "బలమైన, వేగవంతమైన కదలిక వల్ల ఈ ఖాళీ ఏర్పడుతుంది. ధర తరచుగా దాన్ని నింపడానికి వెనక్కి వస్తుంది.",
      "ధర gap లోకి వచ్చి, trend దిశలో తిరిగినప్పుడు entry. Gap ను పూర్తిగా దాటి close అయితే idea తప్పు.",
      "నియమం: trend కి అనుకూలంగా ఉన్న FVG లు మాత్రమే. వ్యతిరేక దిశ FVG లు బలహీనం."], "fvg"),
    ("sweep", "Liquidity sweep (fake breakout)",
     "Liquidity sweeps: stop-loss clusters above highs / below lows, the quick break-and-reclaim, entering after the "
     "reclaim candle closes, invalidation beyond the sweep extreme.",
     ["చాలా మంది SL లు నిన్నటి low కింద, high పైన ఉంటాయి. పెద్ద players ధరను అక్కడికి తోసి ఆ orders తీసుకుంటారు.",
      "Level దాటి వెంటనే వెనక్కి వచ్చి close అయితే sweep. అసలు దిశ దానికి వ్యతిరేకం.",
      "Entry: level లోపల candle close అయిన తర్వాతే. SL: sweep extreme (చివరి అంచు) దాటితే.",
      "నియమం: level దాటిన వెంటనే breakout అనుకుని కొనకండి. Candle close కోసం ఎదురుచూడండి."], "sweep"),
    ("ema_pb", "EMA pullback: trend లో తక్కువ ధరలో ఎక్కడం",
     "EMA pullback: in a clear trend, wait for price to come back to the EMA and turn; entry on the turn, stop beyond "
     "the pullback low/high; why chasing extended moves fails.",
     ["Trend ఉన్నప్పుడు ధర EMA (సగటు రేఖ) దగ్గరకు వెనక్కి రావడం సహజం.",
      "అక్కడ trend దిశలో బలమైన candle వస్తే entry. ఇది chase చేయడం కంటే మంచి ధర.",
      "Pullback low (CE కి) కింద SL. EMA ను బలంగా దాటి వెళ్తే trend బలహీనపడింది.",
      "నియమం: EMA నుంచి చాలా దూరం వెళ్ళిన ధరను వెంటాడకండి. తిరిగి వచ్చే వరకు ఆగండి."], "ema_pb"),
    ("expiry", "Expiry రోజు మరియు IV",
     "Expiry-day behaviour (fast theta, gamma spikes), implied volatility rising before events and collapsing after, "
     "why buyers should size down on expiry days.",
     ["Expiry రోజు premium చాలా వేగంగా కరుగుతుంది, ధర కదలికలు కూడా వేగంగా ఉంటాయి.",
      "IV (implied volatility) news ముందు పెరుగుతుంది, తర్వాత పడిపోతుంది. ధర సరైన దిశలో వెళ్ళినా premium తగ్గొచ్చు.",
      "Expiry రోజు buyers size తగ్గించాలి, త్వరగా profit book చేయాలి.",
      "నియమం: expiry రోజు మధ్యాహ్నం 1 గంట తర్వాత కొత్త buy trades జాగ్రత్తగా మాత్రమే."], "expiry"),
    ("liquidity", "Spread, liquidity మరియు order types",
     "Bid-ask spread as an instant cost, choosing liquid strikes (ATM, high OI/volume), limit vs market orders, "
     "slippage on MCX options.",
     ["Spread = ask - bid. కొన్న వెంటనే మీరు అంత నష్టంలో ఉంటారు.",
      "ATM దగ్గర, OI మరియు volume ఎక్కువ ఉన్న strikes లో spread తక్కువ.",
      "Market order కాకుండా limit order వాడండి, ముఖ్యంగా MCX options లో.",
      "నియమం: spread premium లో 2% కంటే ఎక్కువ ఉంటే ఆ strike వదిలేయండి."], "spread"),
    ("mindset", "Trading psychology మరియు journal",
     "Discipline over prediction, following the plan, journaling every trade, handling losing streaks, why process "
     "beats outcome; how to use the daily lesson card as a journal.",
     ["మంచి trader ని ఫలితం కాదు, process నిర్ణయిస్తుంది. Plan ప్రకారం చేసిన నష్టం మంచి trade.",
      "ప్రతి trade తర్వాత ఒక లైన్ రాసుకోండి: ఎందుకు ఎక్కాను, ఎందుకు దిగాను, ఏం నేర్చుకున్నాను.",
      "వరుస నష్టాలు సహజం. 30 trades తర్వాతే strategy ని అంచనా వేయండి, 3 trades తర్వాత కాదు.",
      "నియమం: భయం లేదా దురాశతో plan మార్చకండి. సందేహం వస్తే trade చేయకపోవడమే మంచి నిర్ణయం."], "best_worst"),
]


def week_index(today: date) -> int:
    return max(0, (today - COURSE_START).days // 7) % len(COURSE)


def _f(x):
    try:
        return None if x in ("", None) else float(x)
    except ValueError:
        return None


def last_week_rows(paths, today: date) -> list:
    """Closed trades of the previous 7 days from the ledgers (one row per setup)."""
    start = (today - timedelta(days=7)).isoformat()
    rows, seen = [], set()
    for p in paths:
        try:
            with open(p, newline="") as f:
                for r in csv.DictReader(f):
                    if not (start <= r.get("date", "") < today.isoformat()) or _f(r.get("result_pct")) is None:
                        continue
                    k = r.get("setup_key") or r.get("trade_id")
                    if k in seen:
                        continue
                    seen.add(k)
                    rows.append(r)
        except FileNotFoundError:
            continue
    return rows


def pick_examples(rows: list, kind: str, n: int = 3) -> list:
    res = lambda r: _f(r.get("result_pct")) or 0.0
    sel = {
        "theta": [r for r in rows if r.get("final_status") in ("EOD_CLOSED", "T1_HIT_THEN_EOD")],
        "levels": [r for r in rows if r.get("strategy") in ("orb15", "pivot_break", "sweep", "sweep_fvg", "gap_orb")],
        "losses": [r for r in rows if res(r) < 0],
        "gave_back": [r for r in rows if r.get("final_status") in ("T1_THEN_SL", "T1_THEN_TRAILING_SL")],
        "fvg": [r for r in rows if r.get("strategy") in ("fvg", "sweep_fvg")],
        "sweep": [r for r in rows if r.get("strategy") in ("sweep", "sweep_fvg", "sess_sweep")],
        "ema_pb": [r for r in rows if r.get("strategy") == "ema_pb"],
        "expiry": [r for r in rows if r.get("expiry_day") in ("True", "true", "1")],
        "spread": sorted([r for r in rows if _f(r.get("spread_pct")) is not None], key=lambda r: -_f(r["spread_pct"])),
    }.get(kind) or []
    if not sel:
        ranked = sorted(rows, key=res)
        sel = ([ranked[-1]] if ranked else []) + ([ranked[0]] if len(ranked) > 1 else [])
    keep = ("date", "symbol", "direction", "strike", "strategy", "engine", "alert_time", "entry_price", "stop_loss",
            "target_1", "target_2", "final_status", "result_pct", "mfe_pct", "mae_pct", "spread_pct", "expiry_day",
            "hold_min", "spot_at_alert", "sell_result_pct")
    return [{k: r.get(k) for k in keep if r.get(k) not in (None, "")} for r in sel[:n]]


WEEK_TASK = (
    "Write this week's concept lesson for the student, in Telugu. Return JSON:\n"
    '{"sections":[{"title":"...","points":["..."]}]}\n'
    "Sections with Telugu titles: (1) the concept in simple words; (2) a small worked example with round numbers you "
    "choose (say they are an example); (3) how it showed up in the student's own trades last week (use only the trade "
    "data given; if there are none, say so and skip); (4) 2-3 practical rules; (5) self-check: 3 short questions, then "
    "their answers in a final point that starts with 'సమాధానాలు:'. 2-4 points per section."
)


def lesson_lines(idx: int, examples: list):
    import ai_tutor
    key, title, focus, points, _kind = COURSE[idx]
    lines = ai_tutor._call(WEEK_TASK, {"concept": focus, "telugu_title": title, "week": idx + 1,
                                       "student_trades_last_week": examples})
    if lines:
        return ai_tutor.to_lines(lines), True
    out = [("ఈ వారం concept", "head")] + [(p, "plain") for p in points]
    if examples:
        out.append(("గత వారం మీ trades లో", "head"))
        for e in examples:
            out.append((f"{e.get('date')} {e.get('symbol')} {e.get('strike')} {e.get('direction')} ({e.get('strategy')}): "
                        f"{e.get('final_status')}, premium మీద {e.get('result_pct')}%", "plain"))
    out += [("ఈ వారం సాధన", "head"), ("ప్రతి alert chart లో ఈ concept ఎక్కడ కనిపిస్తుందో వెతకండి, notebook లో ఒక లైన్ రాయండి.", "plain")]
    return out, False


def build_and_send(ledger_paths, today: date = None, idx: int = None, send_photo=None) -> bool:
    import trade_chart
    today = today or date.today()
    idx = week_index(today) if idx is None else idx % len(COURSE)
    key, title, _focus, _points, kind = COURSE[idx]
    examples = pick_examples(last_week_rows(ledger_paths, today), kind)
    lines, by_ai = lesson_lines(idx, examples)
    path = trade_chart.temp_png(f"weekly_lesson_{idx + 1}")
    import ai_tutor
    if not trade_chart.text_card(path, f"వారం {idx + 1}/{len(COURSE)}: {title}",
                                 (f"AI tutor ({ai_tutor.provider()})" if by_ai else "built-in lesson")
                                 + f"  |  {len(examples)} examples from your last week", lines):
        return False
    if send_photo is None:
        import telegram_notify
        send_photo = lambda p, c: telegram_notify.send_photo(p, caption=c)
    send_photo(path, f"Weekly lesson {idx + 1}/{len(COURSE)}: {key} (week of {today - timedelta(days=today.weekday())})")
    return True


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    week = int(sys.argv[2]) - 1 if len(sys.argv) > 2 and sys.argv[2].strip() else None
    ok = build_and_send([os.path.join(root, "ledger", "outcomes.csv"), os.path.join(root, "mcx", "ledger", "outcomes.csv")],
                        idx=week)
    print("sent" if ok else "not sent")
    sys.exit(0 if ok else 1)
