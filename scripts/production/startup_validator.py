"""
production/startup_validator.py
---------------------------------
Part 9, item 4: Startup Validation.

Runs a battery of checks BEFORE the scanner (or dashboard API) starts
doing anything real: configuration, environment variables, broker/
Telegram credentials, folders, permissions, required files/cache,
historical data availability, Python version, and dependencies.
Produces one detailed report; on any CRITICAL failure the caller gets
a StartupValidationError with that report attached, so the process can
exit gracefully (clear message, non-zero exit code) instead of dying
minutes later with a confusing stack trace from whatever happened to
touch the missing piece first.

Purely additive: nothing in main.py / heartbeat_check.py / the
dashboard API is required to call this. Existing behavior (including
config_validation.validate_startup_config(), which this reuses rather
than duplicates) is unchanged for any caller that doesn't opt in.
"""

from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from exceptions import ConfigurationError, StartupValidationError
from production.config_validator import validate_config, Severity

MIN_PYTHON_VERSION = (3, 10)  # exceptions.py's `X | None` annotations require 3.10+


class CheckStatus(Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str


@dataclass
class StartupReport:
    checks: List[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: CheckStatus, message: str) -> None:
        self.checks.append(CheckResult(name, status, message))

    @property
    def critical_failures(self) -> List[CheckResult]:
        return [c for c in self.checks if c.status is CheckStatus.CRITICAL]

    @property
    def warnings(self) -> List[CheckResult]:
        return [c for c in self.checks if c.status is CheckStatus.WARNING]

    @property
    def passed(self) -> bool:
        return len(self.critical_failures) == 0

    def render(self) -> str:
        lines = ["=" * 60, "STARTUP VALIDATION REPORT", "=" * 60]
        icon = {CheckStatus.OK: "OK  ", CheckStatus.WARNING: "WARN", CheckStatus.CRITICAL: "FAIL"}
        for c in self.checks:
            lines.append(f"[{icon[c.status]}] {c.name}: {c.message}")
        lines.append("-" * 60)
        lines.append(
            f"{len(self.checks)} checks, {len(self.critical_failures)} critical, "
            f"{len(self.warnings)} warning(s)."
        )
        lines.append("RESULT: " + ("PASS" if self.passed else "FAIL -- see critical items above"))
        lines.append("=" * 60)
        return "\n".join(lines)


def _check_python_version(report: StartupReport) -> None:
    current = sys.version_info[:2]
    if current < MIN_PYTHON_VERSION:
        report.add(
            "python_version", CheckStatus.CRITICAL,
            f"Python {current[0]}.{current[1]} detected, requires >= "
            f"{MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}.",
        )
    else:
        report.add("python_version", CheckStatus.OK, f"Python {current[0]}.{current[1]}.")


def _check_dependencies(report: StartupReport, extra_modules: Optional[List[str]] = None) -> None:
    required = ["requests", "pyotp"] + (extra_modules or [])
    missing = []
    for mod in required:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        report.add(
            "dependencies", CheckStatus.CRITICAL,
            f"missing required package(s): {', '.join(missing)}. Run pip install -r requirements.txt.",
        )
    else:
        report.add("dependencies", CheckStatus.OK, f"all required packages importable ({', '.join(required)}).")


def _check_env_credentials(report: StartupReport, require_github: bool, require_upstox: bool,
                            require_angelone: bool) -> None:
    try:
        import config_validation
        config_validation.validate_startup_config(
            require_github=require_github, require_upstox=require_upstox,
            require_angelone=require_angelone,
        )
    except ConfigurationError as exc:
        report.add("credentials_and_env", CheckStatus.CRITICAL, str(exc))
    except Exception as exc:  # noqa: BLE001 - never let an unrelated import error crash validation itself
        report.add("credentials_and_env", CheckStatus.WARNING, f"could not fully validate: {exc}")
    else:
        report.add("credentials_and_env", CheckStatus.OK, "all required env vars/secrets present.")


def _check_config_values(report: StartupReport) -> None:
    try:
        cfg_report = validate_config()
    except Exception as exc:  # noqa: BLE001
        report.add("config_values", CheckStatus.WARNING, f"could not run config value checks: {exc}")
        return

    if cfg_report.errors:
        detail = "; ".join(f"{i.field}: {i.message}" for i in cfg_report.errors)
        report.add("config_values", CheckStatus.CRITICAL, f"{len(cfg_report.errors)} error(s) -- {detail}")
    elif cfg_report.warnings:
        detail = "; ".join(f"{i.field}: {i.message}" for i in cfg_report.warnings)
        report.add("config_values", CheckStatus.WARNING, f"{len(cfg_report.warnings)} warning(s) -- {detail}")
    else:
        report.add("config_values", CheckStatus.OK, "no config value issues found.")


def _check_folders_and_permissions(report: StartupReport, folders: List[str]) -> None:
    problems = []
    for folder in folders:
        path = Path(folder)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".startup_write_check"
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            problems.append(f"{folder} ({exc})")
    if problems:
        report.add(
            "folders_and_permissions", CheckStatus.CRITICAL,
            f"not creatable/writable: {'; '.join(problems)}.",
        )
    else:
        report.add("folders_and_permissions", CheckStatus.OK, f"{len(folders)} folder(s) writable: {', '.join(folders)}.")


def _check_required_files(report: StartupReport, files: List[str]) -> None:
    missing = [f for f in files if not Path(f).exists()]
    if missing:
        report.add("required_files", CheckStatus.WARNING, f"not found (will be created on first use): {', '.join(missing)}.")
    else:
        report.add("required_files", CheckStatus.OK, f"all {len(files)} expected file(s) present.")


def _check_historical_data(report: StartupReport, probe: Optional[Callable[[], bool]]) -> None:
    if probe is None:
        report.add("historical_data", CheckStatus.WARNING, "no historical-data probe supplied -- skipped.")
        return
    try:
        ok = probe()
    except Exception as exc:  # noqa: BLE001
        report.add("historical_data", CheckStatus.CRITICAL, f"probe raised: {exc}")
        return
    if ok:
        report.add("historical_data", CheckStatus.OK, "historical data available.")
    else:
        report.add("historical_data", CheckStatus.CRITICAL, "historical data probe reported unavailable.")


def run_startup_validation(
    *,
    require_github: bool = True,
    require_upstox: bool = True,
    require_angelone: bool = True,
    folders: Optional[List[str]] = None,
    required_files: Optional[List[str]] = None,
    extra_dependency_modules: Optional[List[str]] = None,
    historical_data_probe: Optional[Callable[[], bool]] = None,
    raise_on_failure: bool = True,
) -> StartupReport:
    """Runs every startup check and returns the report. If
    raise_on_failure is True (default) and any check is CRITICAL,
    raises StartupValidationError(report=report) so main.py can catch
    it, print report.render(), and sys.exit(1) instead of crashing
    with a stack trace from deep inside the scanner."""
    report = StartupReport()

    _check_python_version(report)
    _check_dependencies(report, extra_dependency_modules)
    _check_env_credentials(report, require_github, require_upstox, require_angelone)
    _check_config_values(report)
    _check_folders_and_permissions(report, folders or ["logs", "cache"])
    _check_required_files(report, required_files or [])
    _check_historical_data(report, historical_data_probe)

    if raise_on_failure and not report.passed:
        raise StartupValidationError(
            f"Startup validation failed: {len(report.critical_failures)} critical issue(s). "
            "See report for details.",
            report=report,
        )
    return report
