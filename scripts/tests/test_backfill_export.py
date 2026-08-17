"""
tests/test_backfill_export.py
------------------------------------------------------------------------------
Plain assert-based test, same convention as this repo's other tests/*.py
files (no pytest dependency). Mocks requests.get entirely -- no real
GitHub API calls.
"""

import io
import json
import os
import sys
import zipfile
from unittest import mock

_here = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_here)
sys.path.insert(0, _scripts_dir)

import backfill_export


def _fake_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_backfill_combines_multiple_runs_into_one_file(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        output_path = os.path.join(tmp, "combined.txt")

        runs_response = {
            "workflow_runs": [
                {"id": 1, "created_at": "2026-08-10T04:00:00Z", "conclusion": "success"},
                {"id": 2, "created_at": "2026-08-11T04:00:00Z", "conclusion": "success"},
            ]
        }
        log_zips = {
            1: _fake_zip({"0_job/1_step.txt": "REJECTED NIFTY CE ATM strike=24600.0\n"}),
            2: _fake_zip({"0_job/1_step.txt": "REJECTED BANKNIFTY PE ATM strike=57500.0\n"}),
        }

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = mock.Mock()
            if "/runs?" in url or url.endswith("/runs"):
                resp.json.return_value = runs_response
                resp.raise_for_status = lambda: None
            elif "/logs" in url:
                run_id = int(url.split("/runs/")[1].split("/logs")[0])
                resp.status_code = 200
                resp.content = log_zips[run_id]
                resp.raise_for_status = lambda: None
            return resp

        with mock.patch.object(backfill_export.requests, "get", side_effect=fake_get):
            count = backfill_export.backfill(
                "owner/repo", "fake-token", "2026-08-10", "2026-08-11",
                output_path=output_path,
            )

        assert count == 2, f"expected 2 runs combined, got {count}"
        content = open(output_path).read()
        assert "RUN 1" in content and "RUN 2" in content, "both run headers must be present"
        assert "NIFTY" in content and "BANKNIFTY" in content, "both runs' log content must be present"
        # oldest first
        assert content.index("RUN 1") < content.index("RUN 2")
    print("test_backfill_combines_multiple_runs_into_one_file passed")


def test_backfill_underlying_filter_keeps_only_matching_lines():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        output_path = os.path.join(tmp, "combined.txt")
        runs_response = {"workflow_runs": [{"id": 1, "created_at": "2026-08-10T04:00:00Z", "conclusion": "success"}]}
        log_zip = _fake_zip({
            "0_job/1_step.txt": "REJECTED NIFTY CE ATM strike=24600.0\nREJECTED BANKNIFTY PE ATM strike=57500.0\n"
        })

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = mock.Mock()
            if "/logs" in url:
                resp.status_code = 200
                resp.content = log_zip
                resp.raise_for_status = lambda: None
            else:
                resp.json.return_value = runs_response
                resp.raise_for_status = lambda: None
            return resp

        with mock.patch.object(backfill_export.requests, "get", side_effect=fake_get):
            backfill_export.backfill(
                "owner/repo", "fake-token", "2026-08-10", "2026-08-10",
                underlying="NIFTY", output_path=output_path,
            )
        content = open(output_path).read()
        assert "REJECTED NIFTY" in content
        assert "BANKNIFTY" not in content, "underlying filter must exclude non-matching lines"
    print("test_backfill_underlying_filter_keeps_only_matching_lines passed")


def test_backfill_missing_logs_does_not_crash():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        output_path = os.path.join(tmp, "combined.txt")
        runs_response = {"workflow_runs": [{"id": 1, "created_at": "2026-08-10T04:00:00Z", "conclusion": "success"}]}

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = mock.Mock()
            if "/logs" in url:
                resp.status_code = 404  # logs expired / unavailable
            else:
                resp.json.return_value = runs_response
                resp.raise_for_status = lambda: None
            return resp

        with mock.patch.object(backfill_export.requests, "get", side_effect=fake_get):
            count = backfill_export.backfill(
                "owner/repo", "fake-token", "2026-08-10", "2026-08-10", output_path=output_path,
            )
        assert count == 1
        content = open(output_path).read()
        assert "no logs available" in content, "a missing-log run must show a visible gap, not crash or vanish silently"
    print("test_backfill_missing_logs_does_not_crash passed")


if __name__ == "__main__":
    test_backfill_combines_multiple_runs_into_one_file()
    test_backfill_underlying_filter_keeps_only_matching_lines()
    test_backfill_missing_logs_does_not_crash()
    print("\nAll backfill_export tests passed.")
