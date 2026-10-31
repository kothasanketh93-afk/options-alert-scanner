"""
app/engines/orb_pivot/ -- ORB15 + classic-pivot-break SHADOW engines (2026-10-31).
------------------------------------------------------------------------------
Two rule sets taken from a 58-month backtest of NIFTY/BANKNIFTY 5-minute
candles (research data on the `research-data` branch): the only two families
that stayed positive in BOTH the 2022-24 train and 2025-26 test halves
(about +0.05..+0.15R per trade on the underlying, before option costs; many
variants were tested, so treat that as a hypothesis, not proof).

  orb15       first 15 minutes' high/low (3 x 5m candles); first 5m close
              outside the range in the direction of the opening gap
              (today's open vs prior close). Target 1.5 x ATR, stop 1.0 x ATR.
  pivot_break classic pivots from the prior day; first fresh 5m close
              through R1 (CE) or S1 (PE) with a confirming candle colour.
              Target 2.0 x ATR, stop 1.0 x ATR.

SHADOW-ONLY: results go to state["orb_pivot_shadow_trades"], log lines use
the ORBPIVOT_SHADOW_ prefix, nothing is ever sent to Telegram.
"""
from . import signals, engine  # noqa: F401
