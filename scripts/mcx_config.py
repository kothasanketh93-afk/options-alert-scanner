"""MCX shadow constants shared by the runner (mcx_main.py) and the reports (mcx_report.py). No heavy imports."""
COMMODITIES = ("CRUDEOIL", "NATURALGAS", "GOLDM", "GOLD")
# Rupee value of a 1.0 move in the quoted premium for ONE lot. Crude: 100 bbl/lot; NatGas: 1250 mmBtu/lot;
# Gold is quoted per 10 g: GOLDM lot = 100 g (x10), GOLD lot = 1 kg (x100).
LOT_MULTIPLIER = {"CRUDEOIL": 100, "NATURALGAS": 1250, "GOLDM": 10, "GOLD": 100}
STRATEGY_LABELS = {"sweep": "Sweep", "sweep_fvg": "Sweep+FVG", "amd": "AMD", "fvg": "FVG", "oi_flow": "OI flow",
                   "sess_orb": "Session ORB", "sess_sweep": "Session sweep", "ema_pb": "EMA pullback", "bos": "Break of structure", "order_block": "Order block"}
NAMES = {"CRUDEOIL": "Crude Oil", "NATURALGAS": "Natural Gas", "GOLDM": "Gold Mini", "GOLD": "Gold"}
BUCKETS = ("0900-1200", "1200-1700", "1700-2100", "2100-2330")   # IST entry time: morning / midday / US-open run-up / US session


def entry_bucket(alert_time) -> str:
    try:
        mins = int(str(alert_time)[11:13]) * 60 + int(str(alert_time)[14:16])
    except ValueError:
        return ""
    for label, lim in (("0900-1200", 12 * 60), ("1200-1700", 17 * 60), ("1700-2100", 21 * 60)):
        if mins < lim:
            return label
    return "2100-2330"


# MCX trading holidays (source: MCX holiday calendar as listed by Upstox, checked 2026-11-13). MCX trades in two sessions
# (morning 09:00-17:00, evening 17:00-23:30); on most exchange holidays only the MORNING session is closed.
# Dates are year-specific: other years fall back to weekday-only checks until this list is refreshed (mcx_main logs a warning).
MCX_HOLIDAYS_YEAR = 2026
MCX_FULL_HOLIDAYS = {"2026-01-26", "2026-04-03", "2026-10-02", "2026-12-25"}                         # both sessions closed
MCX_EVENING_CLOSED = {"2026-01-01"}                                                                     # morning open only
MCX_MORNING_CLOSED = {"2026-03-03", "2026-03-26", "2026-03-31", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26",
                      "2026-09-14", "2026-10-20", "2026-11-10", "2026-11-24"}                         # evening session open

# Wide-stop what-if (2026-11-13): every MCX shadow trade also gets a silent twin (strategy "<name>_wsl") with the stop distance
# multiplied. Same entry, same targets, no Telegram notice: it only answers "are the stops too tight for gold/crude options?".
MCX_WIDE_SL_VARIANT = True
MCX_WIDE_SL_MULT = 2.0
WIDE_SL_SUFFIX = "_wsl"
