"""
auth.py
-------
Single place that reads and validates every credential this project
uses. Before this, market_data.py and telegram_notify.py each read
os.environ directly -- easy to add a third fetcher/notifier later that
forgets to check its token exists, and it fails deep inside a request
with a confusing error instead of failing at startup with a clear one.

ON UPSTOX "TOKEN REFRESH": Upstox access tokens are daily, generated
through an interactive login-and-consent redirect flow -- there is no
refresh-token grant in Upstox's OAuth setup as of this writing, so
there is no unattended call a headless GitHub Actions job can make to
refresh an expired token. What this module does instead:
validate_upstox_token() below makes one cheap authenticated call and
raises a clear AuthenticationError immediately if the token's dead, so
the failure is obvious in the job log. With the Angel One + Upstox
failover (broker_failover.py), Upstox now only needs to be reachable
as the FALLBACK broker -- see get_angelone_credentials() below for why
Angel One doesn't have this same daily-manual-step problem.

ON ANGEL ONE AUTHENTICATION: fully automated, no manual step, ever.
Angel One's login is a direct clientcode+PIN+TOTP POST (not an
OAuth-redirect flow like Upstox), so the TOTP code can be generated
in-process every run via pyotp using a ONE-TIME-SAVED TOTP secret
(the same secret you'd scan into Google Authenticator, saved once as
a GitHub secret instead) -- see angelone.py's login() for where this
is actually used. Nothing here reads or stores an access token for
Angel One; angelone.py logs in fresh at the start of every run.
"""

import os

import pyotp

from exceptions import ConfigurationError


def get_upstox_token() -> str:
    token = os.environ.get("UPSTOX_ACCESS_TOKEN")
    if not token:
        raise ConfigurationError("UPSTOX_ACCESS_TOKEN not set")
    return token


def trading_enabled_today() -> bool:
    """
    Returns False if the SKIP_TRADING_TODAY repo VARIABLE (Settings ->
    Secrets and variables -> Actions -> Variables tab) is set to
    "true" (case-insensitive) -- the documented way to opt OUT of
    trading for the day.

    Deliberately a Variable, not a secret: GitHub never displays a
    secret's current value, so toggling one daily means blindly
    deleting and re-typing it every time (painful, especially from the
    mobile app). A Variable is plaintext, shows its current value, and
    can be flipped with a single edit -- "true" today, "false"
    tomorrow. The ANGEL_*/UPSTOX_* credentials stay set permanently;
    this is a separate, purpose-built on/off switch, not a repurposed
    credential.

    Checked by main.py (skip scanning for the day), heartbeat_check.py
    (skip staleness/reachability checks so an intentional skip day
    doesn't trigger a false alarm), and premarket_check.py (send a
    one-line confirmation instead of running the full diagnostic).
    """
    return os.environ.get("SKIP_TRADING_TODAY", "").strip().lower() != "true"


def get_angelone_credentials() -> dict:
    """
    Returns {"client_code", "pin", "api_key", "totp_now"} -- "totp_now"
    is a zero-arg callable that generates the current 6-digit TOTP
    code fresh (codes are only valid ~30s, so this is a function, not
    a precomputed string -- calling it right before each login attempt,
    as angelone.py does, is required for correctness).

    ANGEL_TOTP_SECRET is the base32 secret Angel One shows you as a QR
    code when you enable TOTP on smartapi.angelone.in/enable-totp --
    scan it into your authenticator app AND save the same underlying
    secret string here (most authenticator apps let you view/export
    the raw secret when adding a new account, or you can decode the QR
    code's otpauth:// URL directly).
    """
    client_code = os.environ.get("ANGEL_CLIENT_CODE")
    pin = os.environ.get("ANGEL_PIN")
    api_key = os.environ.get("ANGEL_API_KEY")
    totp_secret = os.environ.get("ANGEL_TOTP_SECRET")
    missing = [
        name for name, value in (
            ("ANGEL_CLIENT_CODE", client_code), ("ANGEL_PIN", pin),
            ("ANGEL_API_KEY", api_key), ("ANGEL_TOTP_SECRET", totp_secret),
        ) if not value
    ]
    if missing:
        raise ConfigurationError(f"Missing Angel One credentials: {', '.join(missing)}")

    return {
        "client_code": client_code,
        "pin": pin,
        "api_key": api_key,
        "totp_now": lambda: pyotp.TOTP(totp_secret).now(),
    }


def get_telegram_credentials() -> tuple:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        raise ConfigurationError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    return bot_token, chat_id


def get_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ConfigurationError("GITHUB_TOKEN not set")
    return token


def validate_upstox_token(broker) -> None:
    """
    Confirms the current Upstox token is actually usable by making one
    lightweight authenticated call. `broker` is any Broker (see
    broker.py) -- pass an UpstoxBroker instance directly (NOT the
    FailoverBroker wrapper -- this check is specifically about
    Upstox's own token, so it must bypass failover and hit Upstox
    itself even if Angel One is currently the active broker).

    Raises AuthenticationError (via retry_utils, uncaught here on
    purpose) if the broker reports 401/403. Raises nothing on success --
    callers that only want a pass/fail signal should wrap this in
    try/except, not check a return value, so a failure can never be
    silently ignored.
    """
    broker.get_ltp("NSE_INDEX|Nifty 50")  # well-known, always-listed instrument
