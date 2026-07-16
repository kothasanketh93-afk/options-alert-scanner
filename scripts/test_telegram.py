"""
test_telegram.py
-----------------
One-off manual test: sends a single real message to your configured
Telegram chat via the same telegram_notify.send() the live scanner
uses, so a success here means the actual alert path (credentials,
retry logic, chat_id) works end to end -- not just that Telegram's
API is reachable (which is all heartbeat_check.py's getMe probe
verifies).

Run via the test_telegram.yml workflow's "Run workflow" button, or
locally with TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID set in your
environment: `python scripts/test_telegram.py`
"""

from datetime import datetime

import telegram_notify as tg
from exceptions import TelegramFailure, ConfigurationError

if __name__ == "__main__":
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        tg.send(f"✅ Test alert from options-alert-scanner\nSent at: {now}")
        print("Sent OK -- check your Telegram chat.")
    except (TelegramFailure, ConfigurationError) as exc:
        print(f"FAILED: {exc}")
        raise
