"""
logging_setup.py
-----------------
Structured logging with named channels (scanner/tracker/api/telegram/
main), but writing to STDOUT rather than to scanner.log/tracker.log/
api.log/telegram.log/error.log files on disk.

Why: every GitHub Actions run is a fresh, ephemeral container that's
destroyed the moment the job ends. Files written to disk during the
run vanish with it unless a separate "upload-artifact" step captures
them -- which this project doesn't have (adding one is a real option
if you want persisted logs, see the note at the bottom of this file,
but it means downloading a zip from the Actions UI per run, not
tailing a log file). Meanwhile stdout/stderr from every run IS already
captured, timestamped, and kept by GitHub Actions in the workflow run
log -- which is the only thing actually persisted here regardless of
what this project does. Writing separate on-disk log files that get
thrown away every run would be strictly less useful than what you
already get for free, so this module gives you the *structure*
(named channels, consistent format, log levels) without the disk
files that would just disappear.

Usage: get_logger("scanner") anywhere scanner-related code runs 
(or "tracker" / "api" / "telegram" / "main"), then use it like the
standard logging module: logger.info(...), logger.warning(...),
logger.error(...).
"""

import logging
import sys

_CONFIGURED = False


def _configure_once():
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger("trading_system")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(channel: str) -> logging.Logger:
    """channel: one of "scanner", "tracker", "api", "telegram", "main"
    (or anything else -- these five are just the ones this project uses)."""
    _configure_once()
    return logging.getLogger(f"trading_system.{channel}")


# --- If you actually want persisted, downloadable log files later ---
# Add this step to the end of each workflow's job, after the python
# run step, regardless of whether it succeeded (if: always()):
#
#   - name: Upload logs
#     if: always()
#     uses: actions/upload-artifact@v4
#     with:
#       name: run-logs-${{ github.run_id }}
#       path: logs/*.log
#       retention-days: 7
#
# ...and change get_logger()'s handler to a FileHandler writing under
# logs/<channel>.log instead of StreamHandler(stdout). Not done here
# because it adds an artifact-storage cost and a download-to-read
# workflow for zero benefit until you actually need to grep historical
# logs across many past runs rather than just the latest one.
