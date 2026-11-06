"""Send the weekly strategy scorecard image now. Usage: python scripts/send_scorecard_preview.py <state-branch dir> <YYYY-MM-DD>"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategy_scorecard
import telegram_notify as tg


def main(root, day):
    png = os.path.join(tempfile.mkdtemp(), "scorecard.png")
    wk = strategy_scorecard.render_week(os.path.join(root, "ledger", "outcomes.csv"),
                                        os.path.join(root, "mcx", "ledger", "outcomes.csv"), day, png)
    if not wk:
        print("no closed trades that week")
        return 1
    tg.send_photo(png, caption=f"Weekly strategy scorecard {wk[0]} to {wk[1]} (shadow data, provisional)")
    print("sent")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
