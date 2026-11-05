"""Send the MCX daily or weekly one-lot report image now (manual preview workflow).

Usage: python scripts/send_mcx_preview.py <state-branch dir> <YYYY-MM-DD> <daily|weekly>
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcx_report
import telegram_notify as tg


def main(root, day, kind):
    png = os.path.join(tempfile.mkdtemp(), f"mcx_{kind}.png")
    if kind == "daily":
        p = os.path.join(root, "mcx", "state", f"{day}.json")
        if not os.path.exists(p) or not mcx_report.render_daily(json.load(open(p)), png):
            print("no MCX trades for that day")
            return 1
        tg.send_photo(png, caption=f"MCX daily trade report {day} (one lot per trade, shadow data)")
    else:
        data = mcx_report.render_weekly(os.path.join(root, "mcx", "ledger", "outcomes.csv"), day, png)
        if not data:
            print("no closed MCX trades that week")
            return 1
        tg.send_photo(png, caption=f"MCX weekly trade report {data['start']} to {data['end']} (one lot per trade, shadow data)")
    print("sent", kind)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
