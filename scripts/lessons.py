"""Telugu learning text for trade charts and the daily lesson card (added 2026-11-11).

The user is learning to trade from this app, in Telugu. Three things are explained for every focus trade:
  * concept  -- what the strategy is, in plain Telugu (one short paragraph per strategy)
  * plan     -- what happened now, where the idea is wrong, and the premium entry/SL/targets with one-lot rupees
  * review   -- after the trade closes, rule-based observations from the tracked data (no guessing, no hindsight
               "should have"s that the data cannot show)
Technical words that Indian traders use in English (SL, target, entry, premium, CE/PE...) are kept in English.
"""
from datetime import datetime

CONCEPT = {
    "trade_score": "ప్రధాన scanner: trend (EMA), VWAP, volume, OI build-up అన్నీ ఒకే దిశలో ఉన్నప్పుడు మాత్రమే alert. "
                   "అనేక కారణాలు ఒకేసారి కలిసినప్పుడు trade బలంగా ఉంటుంది అనేది ఇక్కడ ఆలోచన.",
    "chase_bypass": "ఇప్పటికే బాగా కదిలిన ధర వెనుక పరిగెత్తే (chase) trade. ఆలస్యంగా ఎక్కితే risk ఎక్కువ, reward తక్కువ.",
    "fib_pivot": "Fibonacci pivot levels: నిన్నటి high, low, close నుంచి లెక్కించే స్థాయిలు. ఆ స్థాయిని candle close తో దాటితే "
                 "ఆ దిశలో కదలిక కొనసాగుతుందని అంచనా.",
    "orb15": "Opening range breakout: మొదటి 15 నిమిషాల high లేదా low ను దాటితే, ఆ రోజు ఆ దిశలో trend రావచ్చని అంచనా.",
    "pivot_break": "Pivot level ను బలంగా దాటిన breakout trade.",
    "sweep": "Liquidity sweep: పెద్ద players ఒక ముఖ్యమైన level (ఉదా. నిన్నటి low) కిందకి ధరను తోసి, అక్కడ ఉన్న stop-losses "
             "ను తీసుకుని, మళ్ళీ పైకి తెస్తారు. Level దాటి వెంటనే వెనక్కి వస్తే, అది fake breakout.",
    "sweep_fvg": "Sweep తర్వాత Fair Value Gap: level ను sweep చేసి, తిరిగి వచ్చేటప్పుడు ఒక వేగవంతమైన candle ఒక ఖాళీ (gap) "
                 "వదిలిపెడితే, ఆ ఖాళీ దగ్గర entry.",
    "amd": "AMD (Accumulation, Manipulation, Distribution): ముందు ధర ఒక range లో కదులుతుంది (accumulation). తర్వాత "
           "range ను ఒక వైపు fake గా దాటుతుంది (manipulation). ఆ తర్వాత అసలు దిశలో కదులుతుంది (distribution).",
    "oi_flow": "OI flow: ATM చుట్టూ ఉన్న call/put open interest ఎటు పెరుగుతుందో చూసి, ఆ దిశలో trade.",
    "fvg": "Fair Value Gap (FVG): ఒక బలమైన candle వల్ల ముందు candle high కి, తర్వాతి candle low కి మధ్య ఖాళీ ఏర్పడుతుంది. "
           "ధర తరచుగా ఆ ఖాళీని నింపడానికి వెనక్కి వస్తుంది. అక్కడ నుంచి అసలు దిశలో entry.",
    "ema_pb": "EMA pullback: trend ఉన్నప్పుడు ధర EMA (సగటు రేఖ) దగ్గరకు కొంచెం వెనక్కి వచ్చి, మళ్ళీ trend దిశలో "
              "తిరిగితే entry. Trend తో పాటు, తక్కువ ధరలో ఎక్కడం.",
    "gap_orb": "Gap and go: market gap తో open అయి, opening range ను అదే దిశలో దాటితే trend కొనసాగుతుందని అంచనా.",
    "bos": "Break of structure: ముందు ఏర్పడిన swing high (లేదా low) ను candle close తో దాటడం, అంటే trend దిశ మారిందని సూచన.",
    "order_block": "Order block: బలమైన కదలికకు ముందు చివరి వ్యతిరేక candle. ధర ఆ zone కి తిరిగి వస్తే, పెద్ద players మళ్ళీ "
                   "ఆర్డర్లు పెడతారని అంచనా.",
    "sess_orb": "Session ORB (MCX): సాయంత్రం session మొదలైనప్పుడు ఏర్పడే range ను దాటిన breakout.",
    "sess_sweep": "Session sweep (MCX): ముందు session high లేదా low ను sweep చేసి వెనక్కి వచ్చిన reversal.",
}

LEVEL_TE = {"PDH": "నిన్నటి high", "PDL": "నిన్నటి low", "ACC_LOW": "Range low", "ACC_HIGH": "Range high",
            "OI_FLOW": "OI flow ధర", "FVG": "Gap అంచు", "BOS": "Structure level", "GAP_OR_HIGH": "Opening range high",
            "GAP_OR_LOW": "Opening range low"}

STATUS_TE = {"T2_HIT": "Target 2 చేరింది ✅", "SL_HIT": "Stop-loss తగిలింది ❌",
             "T1_THEN_SL": "T1 చేరింది, తర్వాత వెనక్కి వచ్చి SL తగిలింది", "T1_THEN_TRAILING_SL": "T1 తర్వాత trailing SL తో exit",
             "TRAILING_SL_HIT": "Trailing SL తో exit", "EOD_CLOSED": "రోజు చివరికి SL/target ఏదీ తగలలేదు, close చేశాం",
             "T1_HIT_THEN_EOD": "T1 చేరింది, రోజు చివర close చేశాం", "NOT_ENTERED": "Entry ధర రాలేదు, trade జరగలేదు"}


def _n(x):
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def _ts(x):
    try:
        return datetime.fromisoformat(str(x))
    except (TypeError, ValueError):
        return None


def concept(strategy: str) -> str:
    return CONCEPT.get(strategy or "", "")


def setup_lines(trade: dict, lot=None) -> list:
    """[(text, kind)] kind in head/plain/warn/plan -- what the setup is, what happened, where it is wrong, the plan."""
    bull = trade.get("direction") == "CE"
    strike = trade.get("strike")
    strike = f"{strike:g}" if isinstance(strike, (int, float)) else strike
    out = [("ఈ setup ఏమిటి?", "head")]
    c = concept(trade.get("strategy"))
    if c:
        out.append((c, "plain"))
    out.append(("ఇప్పుడు ఏమైంది?", "head"))
    lv, lvv = trade.get("level_name"), _n(trade.get("level_value"))
    where = f" {LEVEL_TE.get(lv, lv)} ({lvv:,.2f}) దగ్గర" if lvv is not None and lv else ""
    out.append((f"{trade.get('symbol')}{where} {'bullish (పైకి)' if bull else 'bearish (కిందకి)'} signal వచ్చింది. "
                f"అందుకే {strike} {'CE' if bull else 'PE'} కొనడం. ఇదే view తో option seller అయితే "
                f"{strike} {'PE' if bull else 'CE'} అమ్ముతాడు.", "plain"))
    inv = _n(trade.get("sweep_extreme"))
    if inv is not None:
        out.append((f"ఈ idea ఎప్పుడు తప్పు? Underlying {inv:,.2f} {'కిందకి' if bull else 'పైకి'} వెళ్తే. అక్కడ ఆలోచించకుండా బయటకు రావాలి.",
                    "warn"))
    e, sl, t1, t2 = (_n(trade.get(k)) for k in ("alert_premium", "stop_loss", "target_1", "target_2"))
    if e and sl is not None and t1 is not None:
        pct = lambda x: (x - e) / e * 100
        out.append(("Premium plan", "head"))
        line = (f"Entry {e:g}  |  SL {sl:g} ({pct(sl):+.0f}%)  |  T1 {t1:g} ({pct(t1):+.0f}%)"
                + (f"  |  T2 {t2:g} ({pct(t2):+.0f}%)" if t2 else ""))
        out.append((line, "plan"))
        if lot:
            out.append((f"1 lot: SL తగిలితే నష్టం Rs {abs(sl - e) * lot:,.0f}, T1 చేరితే లాభం Rs {(t1 - e) * lot:,.0f}"
                        + (f", T2 చేరితే Rs {(t2 - e) * lot:,.0f}" if t2 else ""), "plan"))
        if e > sl:
            rr = (t1 - e) / (e - sl)
            msg = (f"Risk:Reward (T1 వరకు) = 1:{rr:.1f}. "
                   + ("1 కంటే తక్కువ ఉంది, అంటే గెలిచే అవకాశం ఎక్కువగా ఉండాలి." if rr < 1 else "సరిపడా ఉంది."))
            out.append((msg, "warn" if rr < 1 else "plain"))
    out.append(("చార్ట్ ఎలా చదవాలి: BUY బాణం = signal candle. Underlying 'BUY పైన/కింద' గీత దాటినప్పుడే entry. "
                "ఎరుపు box = risk (SL వరకు), ఆకుపచ్చ box = reward (target వరకు). T1/T2 ≈ underlying లో సుమారు స్థాయిలు "
                "(option delta తో లెక్కించినవి); అసలు exit premium numbers ప్రకారం.", "rule"))
    out.append(("నియమం: entry ధర దాటి పోయిన తర్వాత వెంటాడి (chase చేసి) కొనకండి. SL ను ముందే నిర్ణయించుకోండి, "
                "తర్వాత మార్చకండి.", "rule"))
    return out


def review_lines(trade: dict, lot=None, ids_only: bool = False) -> list:
    """[(text, kind)] rule-based review of a closed trade from its tracked data (ids_only: just the lesson ids)."""
    status = trade.get("final_status") or trade.get("status")
    entry = _n(trade.get("tracked_entry_price"))
    out = [("ఫలితం", "head"), (STATUS_TE.get(status, str(status)), "good" if status == "T2_HIT" else "plain")]
    mfe, mae = _n(trade.get("max_favorable_pct")), _n(trade.get("max_adverse_pct"))
    t1, sl0 = _n(trade.get("target_1")), _n(trade.get("initial_stop_loss")) or _n(trade.get("stop_loss"))
    t1pct = (t1 - entry) / entry * 100 if t1 and entry else None
    a, e_t = _ts(trade.get("alert_time")), _ts(trade.get("actual_entry_time"))
    import sell_view
    x_t = _ts(sell_view.exit_time(trade))
    lessons = []
    if status in ("T1_THEN_SL", "T1_THEN_TRAILING_SL"):
        lessons.append(("చేతికొచ్చిన లాభం పోయింది. T1 దగ్గర సగం book చేసి, మిగతా దానికి SL ను entry కి మార్చి ఉంటే ఈ trade లో "
                        "నష్టం వచ్చేది కాదు. Exit rule ఎంత ముఖ్యమో ఇది చూపిస్తుంది.", "warn", "gave_back"))
    if status == "SL_HIT" and mfe is not None and mfe < 3:
        lessons.append((f"Entry తర్వాత ధర మనకు అనుకూలంగా దాదాపు కదలలేదు (గరిష్టంగా {mfe:.1f}% మాత్రమే). Setup మొదటి నుంచే "
                        "బలహీనంగా ఉంది. Signal candle బలం, పెద్ద trend దిశ ఒకటేనా అని చూడండి.", "warn", "no_follow"))
    elif status == "SL_HIT" and mfe is not None and t1pct and mfe >= 0.5 * t1pct:
        lessons.append((f"ధర మనకు అనుకూలంగా {mfe:.1f}% వరకు వెళ్ళింది (T1 కి {t1pct:.0f}% కావాలి), కానీ T1 చేరకుండానే వెనక్కి "
                        "తిరిగింది. Target దూరంగా ఉందా, లేక momentum తగ్గిందా అని గమనించండి.", "warn", "near_t1"))
    if a and e_t:
        delay = (e_t - a).total_seconds() / 60
        if delay > 10:
            lessons.append((f"Signal వచ్చిన {delay:.0f} నిమిషాల తర్వాత entry అయింది. ఆలస్యమైన entry లో risk:reward తగ్గుతుంది.",
                            "warn", "late_entry"))
    ap = _n(trade.get("alert_premium"))
    if ap and entry and entry > ap * 1.05:
        lessons.append((f"Alert ధర {ap:g} కంటే {((entry / ap) - 1) * 100:.0f}% ఎక్కువకు entry అయింది (chase). ఎక్కువ ధరలో "
                        "కొంటే SL దగ్గరవుతుంది, లాభం తగ్గుతుంది.", "warn", "chase"))
    sp = _n(trade.get("spread_pct"))
    if sp is not None and sp > 2:
        lessons.append((f"Bid-ask spread {sp:.1f}% ఉంది. అంటే కొన్న వెంటనే ఇంత నష్టం. Liquidity ఎక్కువ ఉన్న strikes ఎంచుకోండి.",
                        "warn", "spread"))
    if trade.get("expiry") and a and str(trade.get("expiry"))[:10] == a.strftime("%Y-%m-%d"):
        lessons.append(("ఈ రోజు expiry. Time decay (theta) చాలా వేగంగా ఉంటుంది, option buyers కి కష్టమైన రోజు.", "warn", "expiry"))
    if e_t and x_t:
        hold = (x_t - e_t).total_seconds() / 60
        if status == "SL_HIT" and hold <= 10:
            lessons.append((f"Entry తర్వాత {hold:.0f} నిమిషాల్లోనే SL తగిలింది. Volatility ఎక్కువగా ఉంది, లేదా SL చాలా దగ్గరగా ఉంది.",
                            "warn", "fast_sl"))
        if status == "EOD_CLOSED" and hold >= 90:
            lessons.append((f"{hold / 60:.1f} గంటలు ఉంచినా ధర పెద్దగా కదలలేదు. Option buyer కి సమయం శత్రువు, "
                            "ప్రతి నిమిషం premium కరుగుతుంది (theta).", "warn", "theta"))
    if status == "T2_HIT":
        lessons.append(("Plan ప్రకారమే జరిగింది. Entry సమయం, signal candle, trend దిశ బాగా ఉన్నాయి. ఈ chart ను "
                        "గుర్తుపెట్టుకోండి, మంచి setup ఇలా ఉంటుంది.", "good", "clean_win"))
        if mae is not None and mae <= -10:
            lessons.append((f"కానీ మధ్యలో ధర {mae:.0f}% వరకు వ్యతిరేకంగా వెళ్ళింది. SL దూరంగా ఉండటం వల్లే ఈ trade నిలిచింది. "
                            "ఇలాంటి సమయంలో భయపడి ముందే బయటకు రాకూడదు.", "plain", "held_drawdown"))
    sv = trade.get("sell_view") or {}
    from outcomes_ledger import result_pct
    res = result_pct(trade)
    if "pct" in sv and res is not None:
        better = "seller కి" if sv["pct"] > res else "buyer కి"
        lessons.append((f"Buy vs sell: కొన్న వాళ్ళకి {res:+.1f}%, అదే సమయంలో {sv.get('option')} అమ్మిన వాళ్ళకి "
                        f"{sv['pct']:+.1f}%. ఈ trade లో {better} మేలు జరిగింది.", "plain", "sell_better" if sv["pct"] > res else "buy_better"))
    if ids_only:
        return [x[2] for x in lessons]
    if lessons:
        out.append(("ఈ trade నుంచి పాఠం", "head"))
        out += [(t, k) for t, k, _i in lessons]
    return out


KIND_COLOUR = {"head": (241, 196, 15), "plain": (232, 235, 241), "warn": (255, 167, 38), "plan": (129, 212, 250),
               "good": (102, 187, 106), "rule": (150, 158, 175)}


def as_info(lines: list) -> list:
    """lessons [(text, kind)] -> trade_chart info lines."""
    return [(t, KIND_COLOUR.get(k, KIND_COLOUR["plain"]), "head" if k == "head" else "") for t, k in lines]


def as_text(lines: list) -> str:
    """lessons -> Telegram HTML text."""
    from html import escape
    out = []
    for t, k in lines:
        out.append(f"\n<b>{escape(t)}</b>" if k == "head" else ("• " + escape(t)))
    return "\n".join(out).strip()


# Daily lesson card: the mistakes that repeated today, in plain Telugu (ids from review_lines(ids_only=True))
PATTERN_TE = {
    "gave_back": "T1 చేరిన తర్వాత లాభం తిరిగి పోయింది → T1 దగ్గర సగం book చేసి, SL ను entry కి మార్చే నియమం పెట్టుకోండి.",
    "no_follow": "Entry తర్వాత ధర అస్సలు కదలలేదు → బలహీనమైన signals. పెద్ద trend దిశకు వ్యతిరేకంగా ఉన్న alerts ను skip చేయండి.",
    "near_t1": "T1 దగ్గరకు వచ్చి వెనక్కి తిరిగింది → target దూరంగా ఉంది. ఇలాంటప్పుడు T1 కి ముందే కొంత book చేయడం గురించి ఆలోచించండి.",
    "late_entry": "Entry ఆలస్యంగా జరిగింది → alert వచ్చిన వెంటనే నిర్ణయం తీసుకోవాలి, లేదా ఆ trade ను వదిలేయాలి.",
    "chase": "Alert ధర కంటే ఎక్కువకు కొన్నారు (chase) → limit order మాత్రమే వాడండి.",
    "spread": "Spread ఎక్కువగా ఉన్న options → ATM దగ్గర, liquidity ఉన్న strikes మాత్రమే.",
    "expiry": "Expiry రోజు trades → option buyers కి theta ప్రమాదం. Expiry రోజు size తగ్గించండి.",
    "fast_sl": "నిమిషాల్లోనే SL తగిలింది → volatility ఎక్కువ ఉన్న సమయాలు (opening, news) జాగ్రత్తగా చూడండి.",
    "theta": "ధర కదలలేదు, premium కరిగింది → గంటసేపు ఏమీ జరగకపోతే బయటకు రావడం మంచిది.",
    "clean_win": "Plan ప్రకారం జరిగిన మంచి trades → వీటి charts ను మళ్ళీ మళ్ళీ చూడండి.",
    "sell_better": "ఈ trades లో option అమ్మిన వాళ్ళకి ఎక్కువ లాభం వచ్చింది → ధర నెమ్మదిగా కదిలే రోజుల్లో sellers కే మేలు.",
}


def day_lessons(trades: list, top: int = 3) -> list:
    """Most repeated lesson ids across today's closed trades -> [(count, text)]."""
    from collections import Counter
    cnt = Counter()
    for t in trades:
        try:
            cnt.update(set(review_lines(t, ids_only=True)))
        except Exception:
            continue
    return [(n, PATTERN_TE[i]) for i, n in cnt.most_common() if i in PATTERN_TE and i not in ("buy_better", "held_drawdown")][:top]
