"""Send the weekly one-lot report for the week containing a date (manual preview workflow).

Usage: python scripts/send_weekly_preview.py <state-branch dir> <YYYY-MM-DD>
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import telegram_notify as tg
import weekly_image
import weekly_report


def main(root, day):
    data = weekly_report.collect(os.path.join(root, "ledger", "outcomes.csv"), os.path.join(root, "instrument_master"), day)
    png = os.path.join(tempfile.mkdtemp(), "weekly.png")
    if not weekly_image.render(data, png):
        print("no closed trades in that week")
        return 1
    tg.send_photo(png, caption=f"Weekly trade report {data['start']} to {data['end']} (one lot per trade, educational, not advice)")
    print("sent weekly image")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
