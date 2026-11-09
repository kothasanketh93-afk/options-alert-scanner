"""Send the intraday P&L snapshot image now (manual preview). Usage: python scripts/send_pnl_preview.py <state-branch dir> <YYYY-MM-DD> <NSE|MCX>"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eod_report
import pnl_snapshot
import telegram_notify as tg
from datetime import datetime, timedelta, timezone


def main(root, day, market):
    sub = os.path.join("mcx", "state") if market == "MCX" else "state"
    path = os.path.join(root, sub, f"{day}.json")
    if not os.path.exists(path):
        print("no state file for that day")
        return 1
    state = json.load(open(path))
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    lots = None
    if market == "NSE":
        files = sorted(glob.glob(os.path.join(root, "instrument_master", "*.json.gz")))
        lots = eod_report.lot_sizes_from_gz(files[-1]) if files else {}
    r = pnl_snapshot.send_snapshot(market, state, now.strftime("%H%M"), lambda p, caption: tg.send_photo(p, caption=caption), tg.send, lots)
    print("sent", r)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
