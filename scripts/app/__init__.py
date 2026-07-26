"""
app/ -- enterprise-structure pilot (2026-07-25).
------------------------------------------------------------------------------
This package is a PILOT: the first business capability migrated into the
target enterprise folder convention (engines/market/brokers/probability/
risk/patterns/institutional/options/replay/dashboard/analytics/config/
common/logging/database/tests), one bounded module at a time, rather than a
single big-bang restructuring of the whole repo.

NAMING NOTE: the requested top-level package name was "scanner/" -- not used
here because `scripts/scanner.py` (the original Trade Score Engine) already
exists, and `scanner.py` + `scanner/` in the same directory is a genuine
Python import collision, not just a style clash: creating a `scanner/`
package here silently made every existing `import scanner` / `from scanner
import current_session` in the codebase resolve to this NEW empty package
instead of the real module, which would have broken main.py, the original
engine, and this very engine's own `from scanner import current_session`
call, all at once, with no error until runtime. Caught and fixed before
anything else was built on top of it -- named this package `app/` instead.

Why a pilot instead of restructuring everything at once: this is a live
trading bot with real Telegram alerts and a GitHub Actions workflow that
invokes `python scripts/main.py` directly. Moving every file's imports in one
untested pass carries real risk of a silent breakage with no way to verify it
short of a live run. Piloting on ONE already-well-isolated module (the Early
Breakout Engine -- see engines/early_breakout/) lets the new conventions get
validated end-to-end (imports resolve, tests pass, main.py still runs) before
any more of the codebase is moved.

Nested under scripts/ (not at the repo root) deliberately: the GitHub Actions
workflow's `python scripts/main.py` puts `scripts/` on sys.path, not the repo
root. Putting this package at the repo root would require changing the
workflow YAML to add the repo root to PYTHONPATH -- and "DO NOT change
existing GitHub Actions" was explicit. Nesting here means zero workflow
changes and zero sys.path changes were needed to make this pilot work.

What did NOT move here: brokers/ (angelone.py, upstox.py, broker.py,
broker_failover.py), the existing Trade Score Engine (scanner.py), config.py,
state.py, tracker.py, telegram_notify.py, and every other top-level
scripts/*.py module. Those still work exactly as they did before this pilot;
nothing about them changed.
"""
