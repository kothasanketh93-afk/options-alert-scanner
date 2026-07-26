"""
app/engines/options_intelligence/ -- Part 5: Options Intelligence Engine.
------------------------------------------------------------------------------
Enterprise-grade analysis of the option CHAIN itself -- OI, PCR, Max
Pain, Greeks, IV, liquidity, premium composition, strike/expiry
quality, and momentum -- consolidated into one "Options Intelligence
Object" per (symbol, direction, moneyness, strike).

ADDITIVE, NOT A REPLACEMENT: this sits alongside filters.py's hard
quality gate and trade_score.py's Trade Score Engine, it does not
replace either. See engine.py's module docstring for exactly how it
plugs into scanner.score_strike() and why nothing existing changes
unless config.OPTIONS_INTELLIGENCE_FILTER_ENABLED is explicitly turned
on.

Modules:
  chain_metrics.py       Option Chain Engine, PCR Engine, Max Pain
                          Engine, Open Interest Engine
  greeks_iv.py            Greeks Engine, Implied Volatility Engine
  liquidity_premium.py    Option Liquidity Engine (thin wrapper over
                          trade_score.liquidity_strength -- no
                          duplicate math), Premium Analysis Engine
  strike_expiry.py        Strike Selection Engine, Expiry Analysis
                          Engine
  momentum.py             Options Momentum Engine (premium/OI/volume/
                          IV momentum, in-process history)
  scoring.py              Options Confirmation Engine, Options Scoring
                          Engine, Options Filter Engine
  engine.py               analyze() -- the single entry point that
                          produces the Standard Output Object

Each module is independently importable/testable, following the same
convention app/engines/early_breakout/ established -- no module here
needs another engine folder's internals, only shared scripts/ modules
(config, logging_setup, filters, trade_score, option_chain).
"""
