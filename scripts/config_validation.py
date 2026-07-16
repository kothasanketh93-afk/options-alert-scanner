"""
config_validation.py
---------------------
Run once at the start of main.py / heartbeat_check.py,
BEFORE any scanning, tracking, or API calls happen. Checks every piece
of required configuration up front and raises one ConfigurationError
listing everything missing, instead of the run failing 5 minutes in
with a confusing error from whichever call happened to need the
missing piece first.

Deliberately checks ALL required items and reports every failure
together (not just the first one found) -- if you're setting this up
fresh and forgot three secrets, you want to fix all three at once, not
run this three times to discover them one at a time.
"""

import os

from exceptions import ConfigurationError


def validate_startup_config(require_github: bool = True, require_upstox: bool = True,
                             require_angelone: bool = True) -> None:
    missing = []

    if require_angelone:
        for name in ("ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_API_KEY", "ANGEL_TOTP_SECRET"):
            if not os.environ.get(name):
                missing.append(name)
    if require_upstox and not os.environ.get("UPSTOX_ACCESS_TOKEN"):
        # Upstox is the FALLBACK broker now (see broker_failover.py) --
        # still required at startup: failover can't fall back to a
        # broker that was never configured, so a missing Upstox token
        # should fail loudly at startup rather than surfacing as a
        # confusing mid-run failure the first time Angel One has a
        # problem and there's nothing to fail over to.
        missing.append("UPSTOX_ACCESS_TOKEN")
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        missing.append("TELEGRAM_BOT_TOKEN")
    if not os.environ.get("TELEGRAM_CHAT_ID"):
        missing.append("TELEGRAM_CHAT_ID")
    if require_github and not os.environ.get("GITHUB_TOKEN"):
        missing.append("GITHUB_TOKEN")
    if require_github and not os.environ.get("REPO_URL"):
        missing.append("REPO_URL")

    if missing:
        raise ConfigurationError(
            "Missing required configuration: " + ", ".join(missing) +
            ". Set these as repository secrets before running."
        )

    # Instrument keys are checked separately, as a WARNING not a hard
    # failure -- a defensive check in case UNDERLYING_INSTRUMENT_KEYS
    # (now in option_chain.py, re-exported through scanner.py) is ever
    # left blank for a new symbol added later. Currently both NIFTY
    # and BANKNIFTY are filled in, so this should be silent in normal
    # operation.
    #
    # Only UNDERLYING_INSTRUMENT_KEYS needs checking here -- the VWAP
    # futures key used to be a second static dict needing a manual
    # monthly update, but is now resolved dynamically each call via
    # resolve_current_month_future() (implemented on both brokers), so
    # there's nothing static to validate for it at startup.
    import scanner
    unset_underlying = [s for s, k in scanner.UNDERLYING_INSTRUMENT_KEYS.items() if not k]
    if unset_underlying:
        print(
            "[config_validation] WARNING: UNDERLYING_INSTRUMENT_KEYS not yet "
            f"configured for: {unset_underlying}. "
            "Scanning will not produce real setups until these are filled in."
        )

    # angelone.py's get_option_chain() now populates real delta/gamma/
    # theta/vega/iv via the Option Greeks endpoint (see angelone.py's
    # module docstring, point 2) instead of hardcoding delta=None.
    # Two things still worth a loud reminder rather than silent trust:
    # (1) that endpoint is confirmed from Angel One's own SmartAPI
    # forum announcement, not the main docs site, so it's slightly
    # less officially-documented than this project's other endpoints;
    # (2) other users have reported it returning the MONTHLY contract's
    # greeks even when a WEEKLY expirydate was requested, unresolved by
    # Angel One as of this writing. If your setups trade the weekly
    # expiry, spot-check one live optionGreek response against another
    # source before trusting the delta/theta gates unattended.
    print(
        "[config_validation] REMINDER: angelone.py's Option Greeks call "
        "(delta/theta gates) is wired to an endpoint confirmed via Angel "
        "One's forum, not their main docs site, and has a reported weekly-"
        "vs-monthly-expiry mismatch on some accounts -- see angelone.py's "
        "module docstring point 2. Verify one live response before relying "
        "on it unattended for real trades."
    )

    # STOCK_SYMBOLS added 2026-07-17 -- unlike NIFTY/BANKNIFTY/MIDCPNIFTY,
    # these have no static instrument key to sanity-check here at
    # startup (resolved dynamically per-run via
    # Broker.resolve_equity_instrument_key()), so the only check
    # possible without making a live API call is this reminder.
    import config as _config
    if getattr(_config, "STOCK_SYMBOLS", None):
        print(
            "[config_validation] REMINDER: config.STOCK_SYMBOLS/"
            "STOCK_STRIKE_STEP are UNVERIFIED placeholders -- confirm each "
            "symbol is still NSE F&O-eligible (this list is revised every "
            "quarter) and each strike step against a live downloaded "
            "instrument master before trusting stock alerts unattended. "
            f"Currently configured: {_config.STOCK_SYMBOLS}"
        )
