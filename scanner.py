import os
import sys
import json
import requests
from datetime import datetime, time, date
from zoneinfo import ZoneInfo
from urllib.parse import quote

IST = ZoneInfo("Asia/Kolkata")

UPSTOX_TOKEN = os.environ["UPSTOX_TOKEN"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {UPSTOX_TOKEN}"
}

UNIVERSE = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank"
}

NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),
    date(2026, 3, 3),
    date(2026, 3, 26),
    date(2026, 3, 31),
    date(2026, 4, 3),
    date(2026, 4, 14),
    date(2026, 5, 1),
    date(2026, 5, 28),
    date(2026, 6, 26),
    date(2026, 9, 14),
    date(2026, 10, 2),
    date(2026, 10, 20),
    date(2026, 11, 10),
    date(2026, 11, 24),
    date(2026, 12, 25)
}

STATE_FILE = "alert_state.json"
COOLDOWN_MINUTES = 30


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=20
    )

    print("Telegram status:", response.status_code)

    if response.status_code != 200:
        print(response.text)

    return response.status_code == 200


def load_alert_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r") as file:
            return json.load(file)

    except Exception as error:
        print("State load error:", str(error))
        return {}


def save_alert_state(state):
    with open(STATE_FILE, "w") as file:
        json.dump(state, file)


def duplicate_alert(name, direction):
    state = load_alert_state()

    key = f"{name}_{direction}"

    previous = state.get(key)

    if not previous:
        return False

    try:
        previous_time = datetime.fromisoformat(previous)
        now = datetime.now(IST)

        elapsed = (
            now - previous_time
        ).total_seconds() / 60

        if elapsed < COOLDOWN_MINUTES:
            print(
                name,
                direction,
                f"Duplicate blocked. {elapsed:.1f} minutes elapsed."
            )

            return True

    except Exception as error:
        print("Duplicate check error:", str(error))

    return False


def record_alert(name, direction):
    state = load_alert_state()

    key = f"{name}_{direction}"

    state[key] = datetime.now(IST).isoformat()

    save_alert_state(state)

    print(
        name,
        direction,
        "Alert state saved."
    )


def get_session():
    now = datetime.now(IST)

    print(
        "IST Time:",
        now.strftime("%Y-%m-%d %H:%M:%S")
    )

    if now.weekday() >= 5:
        print("Weekend. Scanner skipped.")
        return None

    if now.date() in NSE_HOLIDAYS_2026:
        print("NSE trading holiday. Scanner skipped.")
        return None

    current = now.time()

    if time(9, 30) <= current <= time(10, 35):
        return {
            "name": "MORNING PRIME",
            "min_score": 75
        }

    if time(10, 35) < current <= time(11, 35):
        return {
            "name": "MORNING CONTINUATION",
            "min_score": 80
        }

    if time(13, 45) <= current <= time(15, 10):
        return {
            "name": "AFTERNOON",
            "min_score": 80
        }

    return None


def safe(data, *keys, default=0):
    current = data

    for key in keys:
        if not isinstance(current, dict):
            return default

        current = current.get(key)

        if current is None:
            return default

    return current


def ema(values, period):
    if len(values) < period:
        return None

    result = sum(values[:period]) / period

    multiplier = 2 / (period + 1)

    for price in values[period:]:
        result = (
            price * multiplier
            + result * (1 - multiplier)
        )

    return result


def get_candles(instrument_key):
    encoded_key = quote(
        instrument_key,
        safe=""
    )

    url = (
        "https://api.upstox.com/v3/"
        "historical-candle/intraday/"
        f"{encoded_key}/minutes/5"
    )

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    print(
        instrument_key,
        "Candle API:",
        response.status_code
    )

    response.raise_for_status()

    candles = (
        response.json()
        .get("data", {})
        .get("candles", [])
    )

    return list(reversed(candles))


def analyse_trend(candles):
    if len(candles) < 25:
        return None

    closes = [
        float(candle[4])
        for candle in candles
    ]

    highs = [
        float(candle[2])
        for candle in candles
    ]

    lows = [
        float(candle[3])
        for candle in candles
    ]

    volumes = [
        float(candle[5])
        for candle in candles
    ]

    current = closes[-1]

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)

    recent_high = max(
        highs[-6:-1]
    )

    recent_low = min(
        lows[-6:-1]
    )

    avg_volume = (
        sum(volumes[-6:-1]) / 5
    )

    current_volume = volumes[-1]

    volume_expansion = (
        avg_volume > 0
        and current_volume >= avg_volume * 1.25
    )

    return {
        "current": current,
        "ema9": ema9,
        "ema21": ema21,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "volume_expansion": volume_expansion
    }


def get_contracts(instrument_key):
    url = "https://api.upstox.com/v2/option/contract"

    response = requests.get(
        url,
        headers=HEADERS,
        params={
            "instrument_key": instrument_key
        },
        timeout=30
    )

    print(
        instrument_key,
        "Contract API:",
        response.status_code
    )

    response.raise_for_status()

    return response.json().get(
        "data",
        []
    )


def get_nearest_expiry(contracts):
    today = datetime.now(IST).date()

    expiries = []

    for contract in contracts:
        expiry = contract.get("expiry")

        if not expiry:
            continue

        expiry_date = datetime.strptime(
            expiry,
            "%Y-%m-%d"
        ).date()

        if expiry_date >= today:
            expiries.append(expiry)

    expiries = sorted(set(expiries))

    return expiries[0] if expiries else None


def get_option_chain(
    instrument_key,
    expiry
):
    url = "https://api.upstox.com/v2/option/chain"

    response = requests.get(
        url,
        headers=HEADERS,
        params={
            "instrument_key": instrument_key,
            "expiry_date": expiry
        },
        timeout=30
    )

    print(
        instrument_key,
        "Chain API:",
        response.status_code
    )

    response.raise_for_status()

    return response.json().get(
        "data",
        []
    )


def get_atm_data(chain):
    if not chain:
        return None

    spot = safe(
        chain[0],
        "underlying_spot_price"
    )

    if not spot:
        return None

    atm = min(
        chain,
        key=lambda row: abs(
            safe(
                row,
                "strike_price"
            ) - spot
        )
    )

    return {
        "spot": spot,

        "strike": safe(
            atm,
            "strike_price"
        ),

        "ce_ltp": safe(
            atm,
            "call_options",
            "market_data",
            "ltp"
        ),

        "pe_ltp": safe(
            atm,
            "put_options",
            "market_data",
            "ltp"
        ),

        "ce_oi": safe(
            atm,
            "call_options",
            "market_data",
            "oi"
        ),

        "pe_oi": safe(
            atm,
            "put_options",
            "market_data",
            "oi"
        ),

        "ce_volume": safe(
            atm,
            "call_options",
            "market_data",
            "volume"
        ),

        "pe_volume": safe(
            atm,
            "put_options",
            "market_data",
            "volume"
        ),

        "ce_delta": safe(
            atm,
            "call_options",
            "option_greeks",
            "delta"
        ),

        "pe_delta": safe(
            atm,
            "put_options",
            "option_greeks",
            "delta"
        )
    }


def score_setup(trend, option):
    bull_score = 0
    bear_score = 0

    bull_reasons = []
    bear_reasons = []

    current = trend["current"]

    if current > trend["ema9"] > trend["ema21"]:
        bull_score += 25
        bull_reasons.append(
            "5m EMA trend bullish"
        )

    if current < trend["ema9"] < trend["ema21"]:
        bear_score += 25
        bear_reasons.append(
            "5m EMA trend bearish"
        )

    if current > trend["recent_high"]:
        bull_score += 25
        bull_reasons.append(
            "Recent range breakout"
        )

    if current < trend["recent_low"]:
        bear_score += 25
        bear_reasons.append(
            "Recent range breakdown"
        )

    if trend["volume_expansion"]:
        if bull_score > bear_score:
            bull_score += 15
            bull_reasons.append(
                "Volume expansion"
            )

        elif bear_score > bull_score:
            bear_score += 15
            bear_reasons.append(
                "Volume expansion"
            )

    ce_oi = option["ce_oi"]
    pe_oi = option["pe_oi"]

    if (
        ce_oi > 0
        and pe_oi > ce_oi * 1.15
    ):
        bull_score += 15
        bull_reasons.append(
            "ATM PE OI support"
        )

    if (
        pe_oi > 0
        and ce_oi > pe_oi * 1.15
    ):
        bear_score += 15
        bear_reasons.append(
            "ATM CE OI resistance"
        )

    if (
        option["pe_volume"] > 0
        and option["ce_volume"]
        > option["pe_volume"] * 1.10
    ):
        bull_score += 10
        bull_reasons.append(
            "CE volume dominance"
        )

    if (
        option["ce_volume"] > 0
        and option["pe_volume"]
        > option["ce_volume"] * 1.10
    ):
        bear_score += 10
        bear_reasons.append(
            "PE volume dominance"
        )

    if (
        0.35
        <= option["ce_delta"]
        <= 0.70
    ):
        bull_score += 10
        bull_reasons.append(
            "CE delta usable"
        )

    if (
        -0.70
        <= option["pe_delta"]
        <= -0.35
    ):
        bear_score += 10
        bear_reasons.append(
            "PE delta usable"
        )

    if bull_score > bear_score:
        return {
            "direction": "CE",
            "score": min(
                bull_score,
                100
            ),
            "reasons": bull_reasons
        }

    if bear_score > bull_score:
        return {
            "direction": "PE",
            "score": min(
                bear_score,
                100
            ),
            "reasons": bear_reasons
        }

    return {
        "direction": "NONE",
        "score": 0,
        "reasons": []
    }


def calculate_levels(premium):
    premium = float(premium)

    return {
        "entry_low":
            round(premium * 0.99, 2),

        "entry_high":
            round(premium * 1.02, 2),

        "stop_loss":
            round(premium * 0.85, 2),

        "target1":
            round(premium * 1.20, 2),

        "target2":
            round(premium * 1.35, 2)
    }


def scan_symbol(
    name,
    instrument_key,
    session
):
    print("\nScanning:", name)

    candles = get_candles(
        instrument_key
    )

    trend = analyse_trend(
        candles
    )

    if not trend:
        print(
            name,
            "Not enough candle data"
        )
        return

    contracts = get_contracts(
        instrument_key
    )

    expiry = get_nearest_expiry(
        contracts
    )

    if not expiry:
        print(
            name,
            "No active expiry"
        )
        return

    chain = get_option_chain(
        instrument_key,
        expiry
    )

    option = get_atm_data(
        chain
    )

    if not option:
        print(
            name,
            "No ATM option data"
        )
        return

    signal = score_setup(
        trend,
        option
    )

    print(
        name,
        signal["direction"],
        signal["score"]
    )

    if signal["score"] < session["min_score"]:
        print(
            name,
            "No qualifying setup"
        )
        return

    direction = signal["direction"]

    if direction == "CE":
        premium = option["ce_ltp"]

    elif direction == "PE":
        premium = option["pe_ltp"]

    else:
        return

    if not premium or premium <= 0:
        print(
            name,
            "Invalid premium"
        )
        return

    if duplicate_alert(
        name,
        direction
    ):
        return

    levels = calculate_levels(
        premium
    )

    reasons = "\n".join(
        f"• {reason}"
        for reason in signal["reasons"]
    )

    message = f"""
🚨 STRONG OPTIONS SETUP

Underlying: {name}
Session: {session['name']}

Direction: {direction}

Spot: {option['spot']}
Expiry: {expiry}
ATM Strike: {option['strike']}

Current Premium: ₹{premium}

Entry Zone:
₹{levels['entry_low']} - ₹{levels['entry_high']}

Stop Loss:
₹{levels['stop_loss']}

Target 1:
₹{levels['target1']}

Target 2:
₹{levels['target2']}

Setup Score:
{signal['score']}/100

Minimum Required:
{session['min_score']}/100

Reasons:
{reasons}

Rule-based scanner alert.
Score is not guaranteed win probability.
"""

    if send_telegram(message):
        record_alert(
            name,
            direction
        )


def main():
    session = get_session()

    if session is None:
        print(
            "Scanner inactive. Exit."
        )
        sys.exit(0)

    print(
        "Session:",
        session["name"]
    )

    print(
        "Minimum Score:",
        session["min_score"]
    )

    for name, instrument_key in UNIVERSE.items():
        try:
            scan_symbol(
                name,
                instrument_key,
                session
            )

        except Exception as error:
            print(
                name,
                "ERROR:",
                str(error)
            )

    print(
        "\nScanner run completed."
    )


if __name__ == "__main__":
    main()
