"""MCX shadow constants shared by the runner (mcx_main.py) and the reports (mcx_report.py). No heavy imports."""
COMMODITIES = ("CRUDEOIL", "NATURALGAS", "GOLDM", "GOLD")
# Rupee value of a 1.0 move in the quoted premium for ONE lot. Crude: 100 bbl/lot; NatGas: 1250 mmBtu/lot;
# Gold is quoted per 10 g: GOLDM lot = 100 g (x10), GOLD lot = 1 kg (x100).
LOT_MULTIPLIER = {"CRUDEOIL": 100, "NATURALGAS": 1250, "GOLDM": 10, "GOLD": 100}
STRATEGY_LABELS = {"sweep": "Sweep", "sweep_fvg": "Sweep+FVG", "amd": "AMD", "fvg": "FVG", "oi_flow": "OI flow"}
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
