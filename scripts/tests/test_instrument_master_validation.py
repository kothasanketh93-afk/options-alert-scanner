"""
tests/test_instrument_master_validation.py
------------------------------------------------------------------------------
Plain assert-based tests, same convention as this repo's other tests/*.py
files (no pytest dependency).

Covers the 2026-08-20 root-cause-audit item #4 fix:
instrument_master._validate_master_rows() and download_and_cache_
instrument_master()'s validate-before-overwrite behavior.
"""

import os
import sys
from unittest import mock

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

import instrument_master
import config


def _good_rows(n_index=50, n_filler=5000):
    rows = []
    for i in range(n_index):
        rows.append({"symbol": f"NIFTY24AUG{24000+i*50}CE", "underlying_symbol": "Nifty 50", "contract_class": "INDEX_OPTION"})
        rows.append({"symbol": f"BANKNIFTY24AUG{51000+i*100}PE", "underlying_symbol": "Nifty Bank", "contract_class": "INDEX_OPTION"})
    rows += [{"symbol": "FILLER", "underlying_symbol": "X", "contract_class": "EQUITY"}] * n_filler
    return rows


def test_a_valid_master_passes():
    ok, reason = instrument_master._validate_master_rows(_good_rows(), "2026-08-20")
    assert ok is True
    print("test_a_valid_master_passes passed")


def test_b_too_few_total_rows_fails():
    tiny = [{"symbol": "X", "underlying_symbol": "X", "contract_class": "EQUITY"}] * 10
    ok, reason = instrument_master._validate_master_rows(tiny, "2026-08-20")
    assert ok is False
    assert "total rows" in reason
    print("test_b_too_few_total_rows_fails passed")


def test_c_missing_nifty_fno_fails():
    # Reproduces the ACTUAL observed bug shape: plenty of total rows,
    # but zero F&O rows for the required index.
    rows = [{"symbol": "TATASTEEL", "underlying_symbol": "TATA STEEL LIMITED", "contract_class": "EQUITY"}] * 6000
    ok, reason = instrument_master._validate_master_rows(rows, "2026-08-20")
    assert ok is False
    assert "NIFTY" in reason
    print("test_c_missing_nifty_fno_fails passed")


def test_d_missing_only_banknifty_fno_fails():
    rows = [{"symbol": f"NIFTY24AUG{24000+i*50}CE", "underlying_symbol": "Nifty 50", "contract_class": "INDEX_OPTION"} for i in range(50)]
    rows += [{"symbol": "FILLER", "underlying_symbol": "X", "contract_class": "EQUITY"}] * 6000
    ok, reason = instrument_master._validate_master_rows(rows, "2026-08-20")
    assert ok is False
    assert "BANKNIFTY" in reason
    print("test_d_missing_only_banknifty_fno_fails passed")


def test_d_variant_malformed_payload_fails_without_crashing():
    """STEP 1 case D: rows missing expected keys entirely (a genuinely
    malformed/corrupt payload) must fail validation cleanly -- never
    crash, and never be mistaken for valid."""
    malformed = [{}] * 6000  # no symbol/underlying_symbol/contract_class at all
    ok, reason = instrument_master._validate_master_rows(malformed, "2026-08-20")
    assert ok is False
    assert "NIFTY" in reason
    print("test_d_variant_malformed_payload_fails_without_crashing passed")


def test_e_download_skips_persist_on_invalid_and_falls_back():
    """
    End-to-end: an INVALID download must not call _CACHE.write() at all,
    and must return the previous day's known-good cached rows instead.
    """
    bad_rows = [{"symbol": "TATASTEEL", "underlying_symbol": "TATA STEEL LIMITED", "contract_class": "EQUITY"}] * 6000
    good_previous_day = {"trading_date": "2026-08-19", "row_count": 100, "rows": _good_rows(n_filler=100)}

    class FakeBroker:
        def download_instrument_master(self):
            return bad_rows

    with mock.patch.object(instrument_master.market_data, "get_broker", return_value=FakeBroker()), \
         mock.patch.object(instrument_master._CACHE, "write") as mock_write, \
         mock.patch.object(instrument_master._CACHE, "read") as mock_read:
        mock_read.side_effect = lambda date_str: good_previous_day if date_str == "2026-08-19" else None
        result = instrument_master.download_and_cache_instrument_master("2026-08-20")

    mock_write.assert_not_called()
    assert result == good_previous_day["rows"], "must fall back to the previous day's known-good cached rows"
    print("test_e_download_skips_persist_on_invalid_and_falls_back passed")


def test_f_download_persists_normally_when_valid():
    good_rows = _good_rows(n_filler=6000)

    class FakeBroker:
        def download_instrument_master(self):
            return good_rows

    with mock.patch.object(instrument_master.market_data, "get_broker", return_value=FakeBroker()), \
         mock.patch.object(instrument_master._CACHE, "write", return_value=True) as mock_write:
        result = instrument_master.download_and_cache_instrument_master("2026-08-20")

    mock_write.assert_called_once()
    assert result == good_rows
    print("test_f_download_persists_normally_when_valid passed")


def test_g_no_fallback_available_returns_unpersisted_invalid_data():
    """When even the fallback lookup finds nothing, the run still gets
    SOMETHING back (today's invalid download, in-process only) rather
    than nothing at all -- but it must never be persisted."""
    bad_rows = [{"symbol": "TATASTEEL", "underlying_symbol": "TATA STEEL LIMITED", "contract_class": "EQUITY"}] * 6000

    class FakeBroker:
        def download_instrument_master(self):
            return bad_rows

    with mock.patch.object(instrument_master.market_data, "get_broker", return_value=FakeBroker()), \
         mock.patch.object(instrument_master._CACHE, "write") as mock_write, \
         mock.patch.object(instrument_master._CACHE, "read", return_value=None):
        result = instrument_master.download_and_cache_instrument_master("2026-08-20")

    mock_write.assert_not_called()
    assert result == bad_rows
    print("test_g_no_fallback_available_returns_unpersisted_invalid_data passed")


if __name__ == "__main__":
    test_a_valid_master_passes()
    test_b_too_few_total_rows_fails()
    test_c_missing_nifty_fno_fails()
    test_d_missing_only_banknifty_fno_fails()
    test_d_variant_malformed_payload_fails_without_crashing()
    test_e_download_skips_persist_on_invalid_and_falls_back()
    test_f_download_persists_normally_when_valid()
    test_g_no_fallback_available_returns_unpersisted_invalid_data()
    print("\nAll instrument-master-validation tests (A-G) passed.")
