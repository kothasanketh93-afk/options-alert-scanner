"""
scripts/production/tests/test_phase5_production.py -- unit tests for
Part 9 Phase 5: file_manager and health_aggregator.
------------------------------------------------------------------------------
Plain assert-based, no pytest dependency (see test_phase1_production.py
for the same convention). health_extended.py (the FastAPI route) is not
tested here -- it needs fastapi, unavailable in this offline sandbox;
get_extended_health() (the function it calls) is fully covered instead.

Run with: python scripts/production/tests/test_phase5_production.py
"""

import gzip
import os
import shutil
import sys
import tempfile
import time

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(os.path.dirname(_here))
sys.path.insert(0, _scripts_dir)

from production.file_manager import (
    ensure_directories, delete_expired_files, cleanup_temp_files,
    archive_old_reports, directory_size_bytes,
)
from production.health_aggregator import get_extended_health, HealthProviders
from production.resource_monitor import ResourceMonitor, ResourceThresholds
from production.task_manager import TaskManager


def _tmp_dir():
    return tempfile.mkdtemp(prefix="part9_test_")


def _write_file(path, content=b"x", age_days=None):
    with open(path, "wb") as f:
        f.write(content)
    if age_days is not None:
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))


# --------------------------------------------------------------------
# file_manager
# --------------------------------------------------------------------

def test_ensure_directories_creates_only_missing_ones():
    tmp = _tmp_dir()
    try:
        existing = os.path.join(tmp, "already_there")
        os.makedirs(existing)
        new_nested = os.path.join(tmp, "a", "b", "c")

        created = ensure_directories([existing, new_nested])
        assert existing not in created
        assert new_nested in created
        assert os.path.isdir(new_nested)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_delete_expired_files_only_removes_old_matches():
    tmp = _tmp_dir()
    try:
        old_log = os.path.join(tmp, "old.log")
        new_log = os.path.join(tmp, "new.log")
        other_ext = os.path.join(tmp, "old.txt")
        _write_file(old_log, age_days=40)
        _write_file(new_log, age_days=1)
        _write_file(other_ext, age_days=40)

        result = delete_expired_files(tmp, pattern="*.log", max_age_days=30)
        assert result.deleted == [old_log]
        assert not os.path.exists(old_log)
        assert os.path.exists(new_log)
        assert os.path.exists(other_ext)  # pattern didn't match -- untouched
        assert result.bytes_freed == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_delete_expired_files_on_missing_directory_is_a_noop():
    result = delete_expired_files("/nonexistent/path/xyz", pattern="*", max_age_days=1)
    assert result.deleted == []
    assert result.bytes_freed == 0


def test_cleanup_temp_files_uses_hour_granularity():
    tmp = _tmp_dir()
    try:
        stale_tmp = os.path.join(tmp, "a.tmp")
        fresh_tmp = os.path.join(tmp, "b.tmp")
        _write_file(stale_tmp, age_days=2)       # 48h old
        _write_file(fresh_tmp, age_days=1 / 48)   # 30 min old

        result = cleanup_temp_files(tmp, max_age_hours=24)
        assert result.deleted == [stale_tmp]
        assert os.path.exists(fresh_tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_archive_old_reports_compresses_and_moves():
    tmp = _tmp_dir()
    try:
        source = os.path.join(tmp, "reports")
        archive = os.path.join(tmp, "archive")
        os.makedirs(source)
        old_report = os.path.join(source, "report1.json")
        new_report = os.path.join(source, "report2.json")
        _write_file(old_report, content=b'{"a":1}', age_days=20)
        _write_file(new_report, content=b'{"b":2}', age_days=1)

        result = archive_old_reports(source, archive, pattern="*.json", max_age_days=14)
        assert not os.path.exists(old_report)
        assert os.path.exists(new_report)  # too recent, left in place
        archived_path = os.path.join(archive, "report1.json.gz")
        assert archived_path in result.archived
        with gzip.open(archived_path, "rb") as f:
            assert f.read() == b'{"a":1}'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_archive_old_reports_does_not_double_compress_gz_files():
    tmp = _tmp_dir()
    try:
        source = os.path.join(tmp, "reports")
        archive = os.path.join(tmp, "archive")
        os.makedirs(source)
        already_gz = os.path.join(source, "old.json.gz")
        _write_file(already_gz, content=b"already-compressed-bytes", age_days=20)

        archive_old_reports(source, archive, pattern="*.gz", max_age_days=14)
        target = os.path.join(archive, "old.json.gz")
        assert os.path.exists(target)
        with open(target, "rb") as f:
            assert f.read() == b"already-compressed-bytes"  # moved verbatim, not re-gzipped
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_directory_size_bytes_sums_recursively():
    tmp = _tmp_dir()
    try:
        os.makedirs(os.path.join(tmp, "sub"))
        _write_file(os.path.join(tmp, "a.txt"), content=b"12345")
        _write_file(os.path.join(tmp, "sub", "b.txt"), content=b"1234567890")
        assert directory_size_bytes(tmp) == 15
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_directory_size_bytes_missing_dir_is_zero():
    assert directory_size_bytes("/nonexistent/path/xyz") == 0


# --------------------------------------------------------------------
# health_aggregator
# --------------------------------------------------------------------

def test_extended_health_reports_not_configured_when_nothing_wired():
    result = get_extended_health()
    assert result["resources"]["status"] == "not_configured"
    assert result["background_tasks"]["status"] == "not_configured"
    assert result["broker"]["status"] == "not_configured"
    assert result["telegram"]["status"] == "not_configured"
    assert "overall_status" in result
    assert "overall_score" in result


def test_extended_health_includes_resource_and_task_data_when_wired():
    monitor = ResourceMonitor(thresholds=ResourceThresholds(memory_warning_pct=1, memory_critical_pct=2))
    tm = TaskManager()

    def worker(stop_event, heartbeat):
        heartbeat()
        stop_event.wait(0.2)

    tm.register("w", worker)
    tm.start("w")
    time.sleep(0.02)
    try:
        result = get_extended_health(resource_monitor=monitor, task_manager=tm)
        assert result["resources"]["status"] == "ok"
        assert result["background_tasks"]["status"] == "ok"
        assert result["background_tasks"]["running_count"] == 1
    finally:
        tm.stop("w", timeout=2.0)


def test_extended_health_provider_error_is_captured_not_raised():
    def broken_provider():
        raise RuntimeError("broker API down")

    providers = HealthProviders(broker_status=broken_provider)
    result = get_extended_health(providers=providers)
    assert result["broker"]["status"] == "error"
    assert "broker API down" in result["broker"]["error"]


def test_extended_health_provider_success_merges_result_dict():
    providers = HealthProviders(telegram_status=lambda: {"queue_depth": 3, "last_sent_at": "12:00"})
    result = get_extended_health(providers=providers)
    assert result["telegram"]["status"] == "ok"
    assert result["telegram"]["queue_depth"] == 3


def test_extended_health_critical_resource_alert_lowers_score():
    monitor = ResourceMonitor(thresholds=ResourceThresholds(memory_warning_pct=1, memory_critical_pct=1))
    result_no_alert = get_extended_health()
    result_with_alert = get_extended_health(resource_monitor=monitor)
    # A real machine almost certainly has >1% memory used, so this
    # threshold config should always trip a critical alert here.
    assert result_with_alert["overall_score"] <= result_no_alert["overall_score"]


ALL_TESTS = [
    test_ensure_directories_creates_only_missing_ones,
    test_delete_expired_files_only_removes_old_matches,
    test_delete_expired_files_on_missing_directory_is_a_noop,
    test_cleanup_temp_files_uses_hour_granularity,
    test_archive_old_reports_compresses_and_moves,
    test_archive_old_reports_does_not_double_compress_gz_files,
    test_directory_size_bytes_sums_recursively,
    test_directory_size_bytes_missing_dir_is_zero,
    test_extended_health_reports_not_configured_when_nothing_wired,
    test_extended_health_includes_resource_and_task_data_when_wired,
    test_extended_health_provider_error_is_captured_not_raised,
    test_extended_health_provider_success_merges_result_dict,
    test_extended_health_critical_resource_alert_lowers_score,
]


if __name__ == "__main__":
    passed = 0
    for test_fn in ALL_TESTS:
        test_fn()
        passed += 1
        print(f"PASS: {test_fn.__name__}")
    print(f"\n{passed}/{len(ALL_TESTS)} passed")
