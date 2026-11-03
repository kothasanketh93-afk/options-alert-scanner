"""Send the end-of-day report for a past date as a one-off preview (manual workflow only).

Usage: python scripts/send_eod_preview.py <path to state/YYYY-MM-DD.json>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eod_report
import telegram_notify as tg


def main(path):
    state = json.load(open(path))
    msgs = eod_report.build_messages(state, state.get("date", "?"))
    if not msgs:
        print("no trades in that state file")
        return 1
    import tempfile
    import eod_image
    png = os.path.join(tempfile.mkdtemp(), "eod.png")
    eod_image.render(state, state.get("date", "?"), png)
    tg.send_photo(png, caption=f"End-of-day trade report {state.get('date')} (image version, sample preview)")
    print("sent image")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
