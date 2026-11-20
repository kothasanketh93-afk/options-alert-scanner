"""One-off: send a document from the repo to Telegram (manual workflow only)."""
import sys
import telegram_notify as tg
tg.allow_all()   # manual one-off script: always sends, regardless of config.TELEGRAM_EOD_ONLY

if __name__ == "__main__":
    path, caption = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else None)
    tg.send_document(path, caption)
