"""Alert latency stamps: how long from the moment the trade condition was true until the alert was actually sent.

Two measurements are stored on each trade the moment its alert goes out:
  detect_to_send_sec  = alert_time (the scan cycle that found the setup) -> notification sent.   Pure in-code delay.
  signal_to_send_sec  = close of the signal candle -> notification sent.   Includes the GitHub Actions schedule delay (a run that
                        starts late sees the candle late). The signal candle comes from trade["signal_ts"] when the engine records
                        it (ORB/VWAP family); otherwise it is ESTIMATED as the last completed 5-minute boundary before alert_time.
All times are Asia/Kolkata. Never raises -- a latency problem must not block an alert.
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
BAR_MIN = 5


def _dt(x):
    try:
        d = x if isinstance(x, datetime) else datetime.fromisoformat(str(x))
        return d if d.tzinfo else d.replace(tzinfo=IST)
    except (TypeError, ValueError):
        return None


def signal_bar_close(trade: dict):
    """(datetime, estimated?) -- when the signal candle closed."""
    sig = _dt(trade.get("signal_ts"))
    if sig:
        return sig + timedelta(minutes=BAR_MIN), False
    alert = _dt(trade.get("alert_time"))
    if not alert:
        return None, True
    floored = alert.replace(minute=alert.minute - alert.minute % BAR_MIN, second=0, microsecond=0)
    return floored, True


def stamp_notified(trade: dict, sent_at=None):
    """Record when the alert went out and the two latencies on `trade`. Safe to call more than once (first send wins)."""
    try:
        if trade.get("notified_at"):
            return
        sent = _dt(sent_at) or datetime.now(IST)
        trade["notified_at"] = sent.isoformat()
        alert = _dt(trade.get("alert_time"))
        if alert:
            trade["detect_to_send_sec"] = round(max((sent - alert).total_seconds(), 0.0), 1)
        bar, est = signal_bar_close(trade)
        if bar:
            trade["signal_to_send_sec"] = round(max((sent - bar).total_seconds(), 0.0), 1)
            trade["signal_time_estimated"] = est
    except Exception:
        pass
