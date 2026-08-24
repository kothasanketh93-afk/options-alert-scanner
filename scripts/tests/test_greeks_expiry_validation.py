"""
tests/test_greeks_expiry_validation.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers the 2026-08-24 fix: angelone.py's _fetch_option_greeks() used to
trust every response row's strike/optionType blindly, with no check that
the row's OWN expiry actually matched the requested expirydate -- this
module's own docstring already flagged a "weekly vs monthly expiry
mismatch" reported by other Angel One SmartAPI users for this exact
endpoint. A mismatched row is now skipped and counted, never silently
used for delta/theta gating.
"""

import os
import sys
from unittest import mock

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

import angelone


def _make_broker():
    broker = angelone.AngelOneBroker.__new__(angelone.AngelOneBroker)
    broker._greeks_cache = mock.Mock(get=lambda k: None, set=lambda k, v: None)
    broker._headers = lambda: {}
    return broker


def _fake_response(data, status=True):
    resp = mock.Mock()
    resp.json.return_value = {"status": status, "data": data}
    return resp


# ---------------------------------------------------------------------------
# A. Matching expiry -> kept
# ---------------------------------------------------------------------------

def test_a_matching_expiry_row_is_kept():
    data = [{"strikePrice": "24500", "optionType": "CE", "delta": "0.5", "expiry": "28AUG2025"}]
    broker = _make_broker()
    with mock.patch.object(angelone, "request_with_retry", return_value=_fake_response(data)):
        result = broker._fetch_option_greeks("NIFTY", "28AUG2025")
    assert (24500.0, "CE") in result
    assert result[(24500.0, "CE")]["delta"] == 0.5
    print("test_a_matching_expiry_row_is_kept passed")


# ---------------------------------------------------------------------------
# B. Mismatched expiry -> rejected, never used
# ---------------------------------------------------------------------------

def test_b_mismatched_expiry_row_is_rejected():
    data = [{"strikePrice": "24600", "optionType": "PE", "delta": "-0.45", "expiry": "04SEP2025"}]
    broker = _make_broker()
    with mock.patch.object(angelone, "request_with_retry", return_value=_fake_response(data)):
        result = broker._fetch_option_greeks("NIFTY", "28AUG2025")
    assert (24600.0, "PE") not in result, "B: a row whose own expiry doesn't match the request must never be used"
    assert result == {}
    print("test_b_mismatched_expiry_row_is_rejected passed")


# ---------------------------------------------------------------------------
# C. Mixed response -> correct row kept, mismatched row dropped
# ---------------------------------------------------------------------------

def test_c_mixed_response_keeps_only_matching_rows():
    data = [
        {"strikePrice": "24500", "optionType": "CE", "delta": "0.5", "expiry": "28AUG2025"},
        {"strikePrice": "24600", "optionType": "PE", "delta": "-0.45", "expiry": "04SEP2025"},
        {"strikePrice": "24700", "optionType": "CE", "delta": "0.3", "expiry": "28AUG2025"},
    ]
    broker = _make_broker()
    with mock.patch.object(angelone, "request_with_retry", return_value=_fake_response(data)):
        result = broker._fetch_option_greeks("NIFTY", "28AUG2025")
    assert set(result.keys()) == {(24500.0, "CE"), (24700.0, "CE")}
    print("test_c_mixed_response_keeps_only_matching_rows passed")


# ---------------------------------------------------------------------------
# D. No expiry field present at all -> trusted as-is (cannot validate what
#    isn't there), diagnostic log only, no row dropped
# ---------------------------------------------------------------------------

def test_d_no_expiry_field_present_trusts_response_and_logs_diagnostic():
    data = [{"strikePrice": "24500", "optionType": "CE", "delta": "0.5"}]  # no expiry key at all
    broker = _make_broker()
    with mock.patch.object(angelone, "request_with_retry", return_value=_fake_response(data)), \
         mock.patch.object(angelone, "log") as mock_log:
        result = broker._fetch_option_greeks("NIFTY", "28AUG2025")
    assert (24500.0, "CE") in result, "D: with no expiry field to check, the row must still be used (nothing to validate against)"
    assert mock_log.info.called, "D: must log a diagnostic noting validation wasn't possible"
    assert not mock_log.warning.called, "D: must NOT claim a mismatch when there was nothing to compare"
    print("test_d_no_expiry_field_present_trusts_response_and_logs_diagnostic passed")


# ---------------------------------------------------------------------------
# E. Strike/CE-PE mismatch (unrelated to this fix) was already correctly
#    handled before -- confirm it's still fine (a lookup for a
#    strike/side never in the response simply isn't found).
# ---------------------------------------------------------------------------

def test_e_strike_or_side_not_in_response_is_simply_absent():
    data = [{"strikePrice": "24500", "optionType": "CE", "delta": "0.5", "expiry": "28AUG2025"}]
    broker = _make_broker()
    with mock.patch.object(angelone, "request_with_retry", return_value=_fake_response(data)):
        result = broker._fetch_option_greeks("NIFTY", "28AUG2025")
    assert (24500.0, "PE") not in result  # different side, never claimed
    assert (99999.0, "CE") not in result  # different strike, never claimed
    print("test_e_strike_or_side_not_in_response_is_simply_absent passed")


if __name__ == "__main__":
    test_a_matching_expiry_row_is_kept()
    test_b_mismatched_expiry_row_is_rejected()
    test_c_mixed_response_keeps_only_matching_rows()
    test_d_no_expiry_field_present_trusts_response_and_logs_diagnostic()
    test_e_strike_or_side_not_in_response_is_simply_absent()
    print("\nAll Greeks-expiry-validation tests (A-E) passed.")
