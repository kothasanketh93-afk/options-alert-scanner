"""Optional debit-spread version of a BankNifty alert: buy the alerted strike, sell a farther OTM strike of the same expiry.

Why: the long leg alone loses all its time value (theta) quickly and has uncapped-by-structure % risk; the sold leg pays part of the
premium, so max loss = net debit (known up front) and the break-even moves closer. Trade-off: profit is capped at (width - debit) and
there are two more orders to pay brokerage/slippage on. INFORMATION ONLY -- the alert, tracking and the ledger stay single-leg.
"""
import config

WIDTH_STEPS = 3          # sell 3 strikes (300 pts on BankNifty) beyond the bought strike


def plan(direction: str, buy_strike: float, buy_ask: float, sell_strike: float, sell_bid: float, lot: int) -> dict:
    """Numbers for one lot of the spread. None-safe: returns None if the inputs cannot make a sensible debit spread."""
    try:
        debit = float(buy_ask) - float(sell_bid)
        width = abs(float(sell_strike) - float(buy_strike))
    except (TypeError, ValueError):
        return None
    if debit <= 0 or width <= 0 or debit >= width:
        return None
    be = buy_strike + debit if direction == "CE" else buy_strike - debit
    return {"sell_strike": sell_strike, "net_debit": round(debit, 2), "width": width, "max_loss_rs": round(debit * lot),
            "max_profit_rs": round((width - debit) * lot), "breakeven": round(be, 1),
            "reward_risk": round((width - debit) / debit, 2), "lot": lot}


def sell_strike_for(direction: str, buy_strike: float, step: int, steps: int = WIDTH_STEPS) -> float:
    return buy_strike + step * steps if direction == "CE" else buy_strike - step * steps


def suggest(trade: dict, get_chain, extract_leg) -> dict:
    """Build the spread plan for a BankNifty trade using live chain quotes. `get_chain(trade)` -> chain, `extract_leg` = option_chain's.
    Returns the plan dict or None (non-BankNifty, strike not listed, no bid, ...). Never raises."""
    try:
        if trade.get("symbol") != "BANKNIFTY":
            return None
        step = config.STRIKE_STEP["BANKNIFTY"]
        direction, buy = trade["direction"], float(trade["strike"])
        sell = sell_strike_for(direction, buy, step)
        leg = extract_leg(get_chain(trade), sell, direction)
        if not leg:
            return None
        sell_bid = leg.get("bid") or leg.get("ltp")
        buy_ask = trade.get("ask") or trade.get("alert_premium")
        lot = config.INDEX_LOT_SIZE["BANKNIFTY"]
        return plan(direction, buy, buy_ask, sell, sell_bid, lot)
    except Exception:
        return None


def alert_line(sp) -> str:
    """One compact alert line, empty when there is no plan."""
    if not sp:
        return ""
    return (f"Debit-spread option: buy this strike, sell {sp['sell_strike']:.0f} -> net debit ₹{sp['net_debit']} "
            f"(1 lot ₹{sp['max_loss_rs']:,} = max loss), max profit ₹{sp['max_profit_rs']:,}, break-even {sp['breakeven']}, "
            f"R:R {sp['reward_risk']}\n")
