"""Realistic option round-trip cost model (rupees), used by the BankNifty trade log / reports and the daily loss limit.

Rates are those in force from 1 Apr 2026 (NSE circular 02/2026: options STT 0.15% on the SELL premium). Measured 2026-11-22 on the
663-trade outcomes ledger: these charges average ~0.65% of premium; the rest of the real cost is spread + slippage (~1.34 points,
taken from the ledger's own realistic_net_pct). Everything here is an ESTIMATE for judging an edge -- the contract note is the truth.
"""
import config

BROKERAGE_PER_ORDER = getattr(config, "COST_BROKERAGE_PER_ORDER", 20.0)   # flat Rs per executed order (buy + sell = 2 orders)
STT_SELL_PCT = 0.15          # % of sell premium
EXCHANGE_PCT = 0.0355        # % of premium turnover, each side (NSE options, IPFT included)
SEBI_PCT = 0.0001            # % of turnover (Rs 10 / crore)
STAMP_BUY_PCT = 0.003        # % of buy premium
GST_PCT = 18.0               # on brokerage + exchange + SEBI


def charges_rs(entry_value: float, exit_value: float) -> float:
    """Brokerage + STT + exchange + SEBI + stamp + GST for one buy->sell round trip (premium values in Rs, lot included)."""
    b, s = max(entry_value, 0.0), max(exit_value, 0.0)
    brokerage = 2 * BROKERAGE_PER_ORDER
    stt = s * STT_SELL_PCT / 100
    exch = (b + s) * EXCHANGE_PCT / 100
    sebi = (b + s) * SEBI_PCT / 100
    stamp = b * STAMP_BUY_PCT / 100
    gst = (brokerage + exch + sebi) * GST_PCT / 100
    return brokerage + stt + exch + sebi + stamp + gst


def slippage_rs(entry_value: float) -> float:
    """Spread + slippage allowance, round trip, as % of the entry premium value (config.COST_SLIPPAGE_PCT, measured 1.34)."""
    return max(entry_value, 0.0) * getattr(config, "COST_SLIPPAGE_PCT", 1.34) / 100


def trade_pnl(entry_price, result_pct, lot_size: int, lots: int = 1) -> dict:
    """{gross, charges, slippage, net} in Rs for `lots` lots, from the entry premium and the gross result %. None-safe."""
    try:
        qty = int(lot_size) * int(lots)
        entry_value = float(entry_price) * qty
        exit_value = entry_value * (1 + float(result_pct) / 100.0)
    except (TypeError, ValueError):
        return {"gross": None, "charges": None, "slippage": None, "net": None, "exit_price": None}
    gross = exit_value - entry_value
    ch, sl = charges_rs(entry_value, exit_value), slippage_rs(entry_value)
    return {"gross": round(gross, 2), "charges": round(ch, 2), "slippage": round(sl, 2), "net": round(gross - ch - sl, 2),
            "exit_price": round(float(entry_price) * (1 + float(result_pct) / 100.0), 2)}
