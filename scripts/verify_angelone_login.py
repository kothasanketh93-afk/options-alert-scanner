"""
verify_angelone_login.py
------------------------------------------------------------------------------
Tests ONLY the Angel One login step (clientcode + PIN + TOTP), in isolation
from the rest of the scanner -- no candles, no option chain, no scanning
logic. Reuses the exact same AngelOneBroker.login() the live scanner calls,
so a pass/fail here means exactly the same thing it would mid-run.

Run this locally, FROM INSIDE THE scripts/ FOLDER (it imports angelone.py
and exceptions.py as sibling modules, same as main.py does) -- needs the
same 4 env vars the GitHub Actions job sets:

    cd scripts
    export ANGEL_CLIENT_CODE=...
    export ANGEL_PIN=...
    export ANGEL_API_KEY=...
    export ANGEL_TOTP_SECRET=...
    python verify_angelone_login.py

Or as a one-off GitHub Actions manual run: copy trader.yml's `env:` block
into a new workflow that runs this script instead of scripts/main.py, then
trigger it with workflow_dispatch -- faster than waiting for a scan window
and reading through the full scanner log.

Exit code 0 on successful login, 1 on failure (with the actual error
printed -- same AuthenticationError/BrokerAPIError message you'd see
buried in a real run's log).
"""
import sys

from angelone import AngelOneBroker
from exceptions import AuthenticationError, BrokerAPIError, ConfigurationError


def main():
    print("Attempting Angel One login (clientcode + PIN + TOTP)...")
    broker = AngelOneBroker()
    try:
        broker.login()
    except ConfigurationError as e:
        print(f"FAILED -- credential missing/not set: {e}")
        print("Check that all 4 env vars are set: ANGEL_CLIENT_CODE, ANGEL_PIN, "
              "ANGEL_API_KEY, ANGEL_TOTP_SECRET.")
        sys.exit(1)
    except AuthenticationError as e:
        print(f"FAILED -- login rejected: {e}")
        print("Likely cause: wrong PIN, wrong/stale ANGEL_TOTP_SECRET, or the "
              "SmartAPI app is inactive/deleted on smartapi.angelone.in.")
        sys.exit(1)
    except BrokerAPIError as e:
        print(f"FAILED -- Angel One API error: {e}")
        print("Likely cause: Angel One's login endpoint itself is down/rate-limiting, "
              "or ANGEL_API_KEY doesn't match the app the other credentials belong to.")
        sys.exit(1)
    except Exception as e:
        print(f"FAILED -- unexpected error: {type(e).__name__}: {e}")
        sys.exit(1)

    if broker._jwt_token:
        print(f"SUCCESS -- got a JWT token (starts with: {broker._jwt_token[:12]}...).")
        print("Angel One login is working right now.")
        sys.exit(0)
    else:
        print("FAILED -- login() returned without raising, but no JWT token was set. "
              "This shouldn't happen -- inspect angelone.py's login() response handling.")
        sys.exit(1)


if __name__ == "__main__":
    main()
