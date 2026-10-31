# Research: 5-minute backtests (NIFTY, BANKNIFTY, 2022-2026)

Scripts that produced the ORB / pivot / filter-combination results discussed in chat.
Data: `history/{NIFTY,BANKNIFTY}_5m.csv.gz` from the `research-data` branch (not in main).
Run from this folder: `python run.py` (rule families, writes results.pkl), `python combo.py`
(filter combinations, train 2022-24 vs test 2025-26).

Caveats: underlying-level simulation (no option premium); COST=0.10R is an unverified
assumption; many variants were tested, so positive results may be chance; index volume is 0,
so no VWAP; SL assumed first on same-bar ties.
