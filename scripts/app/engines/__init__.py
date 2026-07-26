"""
app/engines/ -- signal-generating engines, one folder per business capability.

Currently just early_breakout/ (the pilot). Each engine folder is expected
to be independently importable and independently testable -- no engine
should need another engine's internals, only shared, non-engine-specific
modules from scripts/ (config, indicators, market_data, option_chain,
filters, levels, trade_score) or from common/ once that's introduced by a
future pilot.
"""
