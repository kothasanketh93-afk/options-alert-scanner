"""
scripts/production/tests/test_phase2_production.py -- unit tests for
Part 9 Phase 2: config_validator and startup_validator.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (see test_phase1_production.py
for the same convention). Uses small fake config namespaces / monkeypatch-
by-hand instead of real credentials, so this runs fully offline.

Run with: python scripts/production/tests/test_phase2_production.py
"""

import os
import shutil
import sys
import tempfile
import types

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.config_validator import validate_config, Severity
from production.startup_validator import (
    run_startup_validation, StartupReport, CheckStatus,
)
from exceptions import StartupValidationError


def _fake_cfg(**overrides):
    """A minimal, valid fake config namespace; overrides replace fields
    to exercise a specific validation rule."""
    base = dict(
        MAX_CONSECUTIVE_SL=2,
        MAX_CONCURRENT_TRADES=4,
        DUPLICATE_ALERT_COOLDOWN_MINUTES=15,
        STRIKE_RANGE=1,
        TOP_N_STOCKS=20,
        DYNAMIC_UNIVERSE_TOP_N=150,
        STOCK_MIN_OI=4000,
        STOCK_MIN_OPTION_VOLUME=400,
        STOCK_MIN_PREMIUM=2,
        MIN_OI=12000,
        MIN_OPTION_VOLUME=4000,
        MIN_PREMIUM=60,
        RETRY_MAX_ATTEMPTS=3,
        RETRY_BACKOFF_SECONDS=1.5,
        STALE_THRESHOLD_MIN=45,
        STARTUP_GRACE_MIN=15,
        STALE_DATA_THRESHOLD_MIN=10,
        FAILOVER_CONSECUTIVE_FAILURE_THRESHOLD=2,
        ANGELONE_HISTORICAL_MIN_INTERVAL_SECONDS=1.0,
        ANGELONE_HISTORICAL_MAX_CALLS_PER_MINUTE=100,
        ENTRY_SCORE=55,
        STRONG_ENTRY=85,
        TELEGRAM_MIN_PROBABILITY=80,
        OPTIONS_INTELLIGENCE_MIN_SCORE=60,
        SYMBOLS=["NIFTY", "BANKNIFTY"],
        STOCK_SYMBOLS=[],
        INDEX_SYMBOLS=["NIFTY", "BANKNIFTY"],
        MARKET_OPEN_HHMM=(9, 15),
        MARKET_CLOSE_HHMM=(15, 30),
        MARKET_CLOSE_HHMM_HEARTBEAT=(15, 30),
        SUMMARY_HHMM=(15, 31),
        STRIKE_STEP={"NIFTY": 50, "BANKNIFTY": 100},
        STOCK_STRIKE_STEP={},
        USE_DYNAMIC_FNO_UNIVERSE=True,
        GAP_MIN_PCT=0.002,
    )
    base.update(overrides)
    ns = types.SimpleNamespace(**base)
    return ns


def test_valid_config_has_no_errors():
    report = validate_config(_fake_cfg())
    assert report.is_valid
    assert report.errors == []


def test_negative_required_field_is_an_error():
    report = validate_config(_fake_cfg(MAX_CONCURRENT_TRADES=-1))
    assert not report.is_valid
    fields = [i.field for i in report.errors]
    assert "MAX_CONCURRENT_TRADES" in fields


def test_duplicate_symbols_detected():
    report = validate_config(_fake_cfg(SYMBOLS=["NIFTY", "NIFTY", "BANKNIFTY"]))
    assert not report.is_valid
    assert any(i.field == "SYMBOLS" for i in report.errors)


def test_invalid_hhmm_detected():
    report = validate_config(_fake_cfg(MARKET_OPEN_HHMM=(25, 99)))
    assert not report.is_valid
    assert any(i.field == "MARKET_OPEN_HHMM" for i in report.errors)


def test_open_after_close_detected():
    report = validate_config(_fake_cfg(MARKET_OPEN_HHMM=(16, 0), MARKET_CLOSE_HHMM=(15, 30)))
    assert not report.is_valid


def test_negative_fraction_is_error_over_one_is_warning():
    report = validate_config(_fake_cfg(GAP_MIN_PCT=-0.01))
    assert any(i.field == "GAP_MIN_PCT" and i.severity is Severity.ERROR for i in report.issues)

    report2 = validate_config(_fake_cfg(GAP_MIN_PCT=1.5))
    assert any(i.field == "GAP_MIN_PCT" and i.severity is Severity.WARNING for i in report2.issues)
    assert report2.is_valid  # a >1 fraction is only a warning, not blocking


def test_zero_strike_step_is_error():
    report = validate_config(_fake_cfg(STRIKE_STEP={"NIFTY": 0}))
    assert not report.is_valid
    assert any("STRIKE_STEP" in i.field for i in report.errors)


def test_strong_entry_not_greater_than_entry_score_is_warning():
    report = validate_config(_fake_cfg(ENTRY_SCORE=90, STRONG_ENTRY=85))
    assert report.is_valid  # warning only, not an error
    assert any(i.field == "STRONG_ENTRY" for i in report.warnings)


# --------------------------------------------------------------------
# startup_validator
# --------------------------------------------------------------------

def test_startup_report_pass_and_fail_semantics():
    report = StartupReport()
    report.add("a", CheckStatus.OK, "fine")
    assert report.passed
    report.add("b", CheckStatus.WARNING, "hmm")
    assert report.passed  # warnings don't fail the report
    report.add("c", CheckStatus.CRITICAL, "broken")
    assert not report.passed
    assert len(report.critical_failures) == 1


def test_run_startup_validation_raises_on_missing_env(monkeypatch_env=None):
    tmp = tempfile.mkdtemp(prefix="part9_startup_test_")
    # Ensure known-missing env vars stay missing for this check by
    # clearing anything that might already be set in this shell.
    saved = {}
    for var in ("ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_API_KEY", "ANGEL_TOTP_SECRET",
                "UPSTOX_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "GITHUB_TOKEN", "REPO_URL"):
        saved[var] = os.environ.pop(var, None)
    try:
        try:
            run_startup_validation(folders=[tmp])
            assert False, "expected StartupValidationError with all broker/telegram env vars missing"
        except StartupValidationError as exc:
            assert exc.report is not None
            assert not exc.report.passed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for var, value in saved.items():
            if value is not None:
                os.environ[var] = value


def test_run_startup_validation_folder_check_fails_on_unwritable_path():
    # A path nested under a file (not a directory) can never be
    # mkdir'd into -- deterministic, portable way to force a folder
    # check failure without touching real permissions.
    tmp = tempfile.mkdtemp(prefix="part9_startup_test_")
    blocker_file = os.path.join(tmp, "blocker")
    with open(blocker_file, "w") as f:
        f.write("x")
    bad_folder = os.path.join(blocker_file, "cannot_exist")
    try:
        report = run_startup_validation(
            require_github=False, require_upstox=False, require_angelone=False,
            folders=[bad_folder], raise_on_failure=False,
        )
        folder_check = [c for c in report.checks if c.name == "folders_and_permissions"][0]
        assert folder_check.status == CheckStatus.CRITICAL
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_historical_data_probe_result_reflected_in_report():
    tmp = tempfile.mkdtemp(prefix="part9_startup_test_")
    try:
        report = run_startup_validation(
            require_github=False, require_upstox=False, require_angelone=False,
            folders=[tmp], raise_on_failure=False,
            historical_data_probe=lambda: False,
        )
        hist_check = [c for c in report.checks if c.name == "historical_data"][0]
        assert hist_check.status == CheckStatus.CRITICAL
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


ALL_TESTS = [
    test_valid_config_has_no_errors,
    test_negative_required_field_is_an_error,
    test_duplicate_symbols_detected,
    test_invalid_hhmm_detected,
    test_open_after_close_detected,
    test_negative_fraction_is_error_over_one_is_warning,
    test_zero_strike_step_is_error,
    test_strong_entry_not_greater_than_entry_score_is_warning,
    test_startup_report_pass_and_fail_semantics,
    test_run_startup_validation_raises_on_missing_env,
    test_run_startup_validation_folder_check_fails_on_unwritable_path,
    test_historical_data_probe_result_reflected_in_report,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
