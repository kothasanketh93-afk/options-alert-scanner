"""Trade record construction shared by the NSE trader (main.py) and the MCX shadow runner (mcx_main.py)."""
from datetime import datetime

TERMINAL_STATUSES = {"T2_HIT", "SL_HIT", "NOT_ENTERED", "AMBIGUOUS", "EOD_CLOSED"}


def new_trade_record(setup: dict, trade_id: str, alert_dt: datetime, display_label: str) -> dict:
    trade = {
        "trade_id": trade_id,
        "display_label": display_label,  # short "Trade N" label for Telegram messages -- see state.next_display_number()
        "symbol": setup["symbol"],
        "direction": setup["direction"],
        "moneyness": setup["moneyness"],
        "instrument_key": setup["instrument_key"],
        "strike": setup["strike"],
        "expiry": setup["expiry"],
        "spot_price": setup["spot_price"],
        "alert_time": alert_dt.isoformat(),
        "alert_premium": setup["alert_premium"],
        "entry_low": setup["entry_low"],
        "entry_high": setup["entry_high"],
        "stop_loss": setup["stop_loss"],
        "initial_stop_loss": setup["stop_loss"],   # stop_loss is raised in place by the trailing logic; this keeps the original risk
        "target_1": setup["target_1"],
        "target_2": setup["target_2"],
        "session": setup["session"],

        "grade": setup["grade"],
        # Phase 3: numeric Trade Score Engine fields -- shown in Telegram
        # per an explicit decision reversing the earlier V2.0 "no raw
        # numeric score ever reaches Telegram" design (see trade_score.py).
        "trade_score": setup["trade_score"],
        "bid": setup.get("bid"), "ask": setup.get("ask"), "spread_pct": setup.get("spread_pct"),
        "probability_pct": setup["probability_pct"],
        "probability_label": setup.get("probability_label"),
        "score_breakdown": setup.get("score_breakdown", []),
        "confidence_score": setup["confidence_score"],
        "risk_score": setup["risk_score"],
        "reward_score": setup["reward_score"],
        "expected_rr": setup["expected_rr"],
        "factor_checklist": setup["factor_checklist"],
        # Backward-compat display fields, still carried too:
        "confidence_stars": setup["confidence_stars"],
        "confidence_label": setup["confidence_label"],
        "risk_rating": setup["risk_rating"],
        "risk_label": setup["risk_label"],
        "expected_holding_minutes": list(setup["expected_holding_minutes"]),
        "why_reasons": setup["why_reasons"],
        "alternatives": setup.get("alternatives", []),

        "status": "WAITING_ENTRY",
        "final_status": None,
        "actual_entry_time": None,
        "tracked_entry_price": None,
        "highest_since_entry": None,
        "lowest_since_entry": None,
        "max_favorable_pct": 0.0,
        "max_adverse_pct": 0.0,
        "t1_hit": False,
        "t1_hit_time": None,
        "t2_hit": False,
        "t2_hit_time": None,
        "sl_hit_time": None,
        "eod_last_price": None,
        "last_processed_ts": alert_dt.isoformat(),

        "messages_sent": [],  # e.g. ["NEW_TRADE_ALERT", "ENTRY_ACTIVATED"]
        "streak_counted": False,
    }

    # Early Breakout Engine (app/engines/early_breakout/, added 2026-07-24,
    # upgraded to the 5-stage Institutional Accumulation pipeline the same
    # day, migrated from scripts/strategy/ to this package on 2026-07-25
    # as the enterprise-structure pilot -- see app/__init__.py): passthrough
    # fields that only this engine's setups carry. "strategy" defaults to "trade_score" for setups from the
    # existing engine so every trade record can be told apart. The
    # rest are only added when present, so this is a no-op for every
    # existing (non-early-breakout) call site -- inert to tracker.py,
    # state.py, and telegram_notify.fmt_new_alert(), which never read
    # these keys.
    trade["strategy"] = setup.get("strategy", "trade_score")
    for optional_key in (
        "early_breakout_reasons", "base_score", "institutional_score",
        "momentum_score", "smart_money_score", "breakout_price",
        "entry_zone", "maximum_entry",
    ):
        if optional_key in setup:
            trade[optional_key] = setup[optional_key]

    return trade
