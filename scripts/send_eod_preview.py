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
    msgs[0] = "🧪 <b>SAMPLE PREVIEW</b> (one-off, today's data)\n" + msgs[0]
    for m in msgs:
        tg.send(m)
    print(f"sent {len(msgs)} message(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
