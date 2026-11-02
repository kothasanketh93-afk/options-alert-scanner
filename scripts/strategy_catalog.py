"""
Strategy & candlestick-pattern catalog (2026-11-02): ONE place that lists every rule set in this project,
what it is, where it is implemented, and what the evidence says. `python scripts/strategy_catalog.py --markdown`
regenerates docs/STRATEGY_CATALOG.md. Evidence = our own 2-year NIFTY option-premium backtest (+15/-10 rule,
actual expired-contract premiums, assumed costs/slippage, ATM, one trade/day, no regime filter unless stated);
every positive number below is a small sample and NOT validated. Educational research, not investment advice.
"""
import sys

STATUS_LIVE, STATUS_SHADOW, STATUS_LAB, STATUS_RESEARCH = "live-alert", "shadow", "signal-lab", "research"

STRATEGIES = [
    # --- live / shadow engines in the app ---
    dict(id="scanner_conditions", name="Main scanner (conditions-based)", status=STATUS_LIVE, family="multi-factor",
         rules="ITM/ATM/OTM strikes pass hard gates (EMA, VWAP, volume, OI build-up, liquidity, R:R, delta, theta, premium range), "
               "direction confirmations >=2/4 incl. structural EMA confirmation, chase/extension filters, Options Intelligence, spread cap.",
         where="scripts/scanner.py", evidence="Shadow sample n=17 (scores did not separate winners/losers); no validated edge."),
    dict(id="orb15", name="ORB15 (opening range 15 min)", status=STATUS_SHADOW, family="breakout",
         rules="First 5m close beyond the first-15-minute high/low; stop 1.0xATR, target 2.0xATR.",
         where="scripts/app/engines/orb_pivot/signals.py", evidence="Underlying-level research only (58-month backtest, +0.05..+0.15R before option costs)."),
    dict(id="pivot_break", name="Classic pivot break (R1/S1)", status=STATUS_SHADOW, family="breakout",
         rules="First fresh 5m close through R1 (CE) or S1 (PE) of prior-day pivots with confirming candle colour.",
         where="scripts/app/engines/orb_pivot/signals.py", evidence="Underlying-level research only."),
    dict(id="pdhl_retest", name="PDH/PDL breakout + retest", status=STATUS_SHADOW, family="breakout-retest",
         rules="Strong breakout close beyond previous-day high/low, retest within 12 candles that holds, EMA9/21 aligned; index options only.",
         where="scripts/app/engines/orb_pivot/signals.py (detect_pdhl_retest)", evidence="Option backtest S3: n=41, 36.6% hit, -Rs137/trade; regime-filtered n=14, 50% (too small)."),
    dict(id="fib_pivot", name="Fibonacci pivot", status=STATUS_SHADOW, family="pivot", rules="Fib pivot levels shadow engine.",
         where="scripts/app/engines/fib_pivot/", evidence="No evidence collected; candidate to retire."),
    dict(id="early_breakout", name="Early institutional breakout", status=STATUS_LIVE, family="breakout",
         rules="Early-session breakout with institutional/base/momentum sub-scores (its own score floor is now optional).",
         where="scripts/app/engines/early_breakout/", evidence="No validated edge."),
    # --- 10 research strategies (frozen rules, option-premium backtest) ---
    dict(id="S1", name="VWAP Pullback Continuation", status=STATUS_RESEARCH, family="trend-pullback",
         rules="Price on one side of a sloping VWAP, EMA9/21 aligned, higher-high/higher-low structure, pullback to VWAP/EMA21, confirming candle.",
         where="research/nifty15/strategies.py", evidence="n=293, 37.9% hit, -Rs118/trade (ATM)."),
    dict(id="S2", name="ORB Retest", status=STATUS_RESEARCH, family="breakout-retest",
         rules="Opening-range breakout then retest that holds.", where="research/nifty15/strategies.py",
         evidence="n=121, 32.2%, -Rs195; regime-filtered n=28, 53.6%, +Rs184 (small sample)."),
    dict(id="S3", name="PDH/PDL Breakout Retest", status=STATUS_RESEARCH, family="breakout-retest",
         rules="Previous-day high/low breakout candle (body>=50%), retest, trend alignment.", where="research/nifty15/strategies.py",
         evidence="n=41, 36.6%, -Rs137; filtered n=14, 50%."),
    dict(id="S4", name="First Pullback After Opening Momentum", status=STATUS_RESEARCH, family="momentum-pullback",
         rules="1xATR open drive on above-normal futures volume, first pullback up to half the move, confirmation candle before 10:45.",
         where="research/nifty15/strategies.py", evidence="n=15, 46.7%, +Rs29 (tiny sample)."),
    dict(id="S5", name="Liquidity Sweep / Failed Breakout", status=STATUS_RESEARCH, family="reversal",
         rules="Sweep beyond opening range then close back inside.", where="research/nifty15/strategies.py", evidence="n=414, 32.6%, -Rs215."),
    dict(id="S6", name="VWAP Reclaim / Breakdown", status=STATUS_RESEARCH, family="vwap", rules="Price reclaims/loses VWAP with EMA9 confirmation.",
         where="research/nifty15/strategies.py", evidence="n=188, 37.8%, -Rs124."),
    dict(id="S7", name="Relative Strength / Sector Momentum", status=STATUS_RESEARCH, family="relative-strength",
         rules="Sector composite stronger than NIFTY and rising, price above EMA21 and day open, breakout of prior candle high.",
         where="research/nifty15/strategies.py", evidence="n=276, 35.1%, -Rs171."),
    dict(id="S8", name="EMA 9/21 Trend Pullback", status=STATUS_RESEARCH, family="trend-pullback",
         rules="EMA9>EMA21, higher-high/higher-low, pullback to EMA9/21, bullish confirmation.", where="research/nifty15/strategies.py",
         evidence="n=476, 35.1%, -Rs158."),
    dict(id="S9", name="Support & Resistance Reversal", status=STATUS_RESEARCH, family="reversal",
         rules="Reversal at prior-day/5-day/opening-range levels with VWAP context.", where="research/nifty15/strategies.py", evidence="n=206, 34%, -Rs191."),
    dict(id="S10", name="Inside Bar / NR7 Breakout", status=STATUS_RESEARCH, family="volatility-breakout",
         rules="Narrow-range / inside-bar compression breakout.", where="research/nifty15/strategies.py", evidence="n=340, 33.5%, -Rs191."),
]
# Signal-Lab (underlying-level, scripts/signal_lab.py, first signal per day, stop 1xATR, targets 1.5/2.0xATR)
for _n, _d in [
    ("orb15_gap", "ORB15 aligned with the opening gap"), ("orb15_gap_trend", "ORB15 + gap + 15m EMA trend"),
    ("pivot_break", "R1/S1 pivot break"), ("pdhl_gap", "PDH/PDL break aligned with gap"),
    ("camarilla_h3l3", "Camarilla H3/L3 reversal"), ("camarilla_h4l4", "Camarilla H4/L4 breakout"),
    ("cpr_narrow", "Narrow CPR breakout"), ("squeeze_gap_trend", "Volatility squeeze + gap + trend"),
    ("engulf_gap_trend", "Engulfing candle + gap + trend"), ("ema_cross_5m", "5m EMA9/21 cross"),
    ("composite_ema_rsi_macd", "Close above EMA21 + RSI>50 + MACD histogram turn"),
    ("rsi_reversion", "RSI 30/70 re-entry"), ("bollinger_squeeze", "Bollinger band-width squeeze breakout"),
    ("bollinger_fade", "Bollinger band re-entry fade"), ("inside_bar", "Inside-bar breakout"),
    ("ema_pullback", "EMA21 pullback in trend")]:
    STRATEGIES.append(dict(id=f"lab:{_n}", name=_n, status=STATUS_LAB, family="lab", rules=_d, where="scripts/signal_lab.py",
                           evidence="Collecting live data (ledger/signal_lab.csv); underlying-level, before option costs."))

PATTERNS = [  # scripts/candlestick_patterns.py
    ("Doji", "neutral", "Body <=10% of range: indecision."), ("Spinning Top", "neutral", "Small body (<=35%) with wicks both sides."),
    ("Hammer", "bullish*", "Small body, long lower wick (>=2x body); bullish after a down-move, neutral after an up-move."),
    ("Shooting Star", "bearish*", "Small body, long upper wick; bearish after an up-move."),
    ("Marubozu", "bull/bear", "Both wicks <=5% of range: strong one-direction candle."),
    ("Bullish Engulfing", "bullish", "Bullish body engulfs prior bearish body."), ("Bearish Engulfing", "bearish", "Bearish body engulfs prior bullish body."),
    ("Inside Bar", "neutral", "Range inside prior candle: compression."), ("Outside Bar", "neutral", "Range engulfs prior candle: expansion."),
    ("Morning Star", "bullish", "Three-candle reversal after a decline."), ("Evening Star", "bearish", "Three-candle reversal after a rise."),
    ("Three White Soldiers", "bullish", "Three consecutive strong bullish candles."), ("Three Black Crows", "bearish", "Three consecutive strong bearish candles."),
]


def markdown() -> str:
    out = ["# Strategy & pattern catalog", "",
           "Generated by `python scripts/strategy_catalog.py --markdown`. Evidence is from our own backtests/shadow data; every number is a",
           "small, unvalidated sample. Educational research, not investment advice.", "",
           "Per-bar record of which patterns/strategies fired: `ledger/pattern_tape/YYYY-MM-DD.csv` (state branch); query with `research/query_tape.py`.", ""]
    for status in (STATUS_LIVE, STATUS_SHADOW, STATUS_RESEARCH, STATUS_LAB):
        rows = [s for s in STRATEGIES if s["status"] == status]
        out += [f"## {status} ({len(rows)})", "", "| id | name | family | rules | where | evidence |", "|---|---|---|---|---|---|"]
        out += [f"| {s['id']} | {s['name']} | {s['family']} | {s['rules']} | `{s['where']}` | {s['evidence']} |" for s in rows]
        out.append("")
    out += ["## Candlestick patterns", "", "| pattern | bias | definition |", "|---|---|---|"]
    out += [f"| {n} | {b} | {d} |" for n, b, d in PATTERNS]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    if "--markdown" in sys.argv:
        sys.stdout.write(markdown())
    else:
        for s in STRATEGIES:
            print(f"{s['status']:11} {s['id']:28} {s['name']}")
