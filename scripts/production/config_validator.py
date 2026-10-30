"""
production/config_validator.py
--------------------------------
Part 9, item 5: Configuration Validation.

Relationship to the existing scripts/config_validation.py
-----------------------------------------------------------
config_validation.validate_startup_config() is untouched -- it still
does exactly what it did before: check required env vars/secrets are
present and raise ConfigurationError listing everything missing. This
module is a separate, additive pass over config.py's actual VALUES
(not env vars): duplicate symbols, negative intervals, invalid
thresholds, invalid market-hour timings, and similar bugs, that
today would only surface as a confusing runtime failure hours into a
scan.

This module never mutates config.py and never raises by itself --
callers (e.g. production.startup_validator) decide what to do with
the report. Nothing in the existing scanner/decision-engine/replay/
analytics/dashboard code has to change to get this: it's opt-in,
read-only introspection of the config module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


class Severity(Enum):
    ERROR = "error"                 # config is broken; startup_validator treats this as critical
    WARNING = "warning"             # config is legal but risky/surprising
    RECOMMENDATION = "recommendation"  # a style/maintenance nudge, never blocks anything


@dataclass
class ConfigIssue:
    severity: Severity
    field: str
    message: str


@dataclass
class ConfigValidationReport:
    issues: List[ConfigIssue] = field(default_factory=list)

    def add(self, severity: Severity, field_name: str, message: str) -> None:
        self.issues.append(ConfigIssue(severity, field_name, message))

    @property
    def errors(self) -> List[ConfigIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> List[ConfigIssue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def recommendations(self) -> List[ConfigIssue]:
        return [i for i in self.issues if i.severity is Severity.RECOMMENDATION]

    @property
    def is_valid(self) -> bool:
        """False only on ERROR-severity issues -- warnings/recommendations
        are advisory and never block startup on their own."""
        return len(self.errors) == 0

    def summary(self) -> str:
        lines = [
            f"Config validation: {len(self.errors)} error(s), "
            f"{len(self.warnings)} warning(s), {len(self.recommendations)} recommendation(s)."
        ]
        for issue in self.issues:
            lines.append(f"  [{issue.severity.value.upper()}] {issue.field}: {issue.message}")
        return "\n".join(lines)


# Fields that must be a positive number (>0). Grown incrementally as
# new config keys are added -- an unlisted numeric field simply isn't
# checked here rather than guessed at.
_MUST_BE_POSITIVE = (
    "MAX_CONSECUTIVE_SL", "MAX_CONCURRENT_TRADES", "DUPLICATE_ALERT_COOLDOWN_MINUTES",
    "STRIKE_RANGE", "TOP_N_STOCKS", "DYNAMIC_UNIVERSE_TOP_N",
    "STOCK_MIN_OI", "STOCK_MIN_OPTION_VOLUME", "STOCK_MIN_PREMIUM",
    "MIN_OI", "MIN_OPTION_VOLUME", "MIN_PREMIUM",
    "RETRY_MAX_ATTEMPTS", "RETRY_BACKOFF_SECONDS",
    "STALE_THRESHOLD_MIN", "STARTUP_GRACE_MIN", "STALE_DATA_THRESHOLD_MIN",
    "FAILOVER_CONSECUTIVE_FAILURE_THRESHOLD",
    "ANGELONE_HISTORICAL_MIN_INTERVAL_SECONDS", "ANGELONE_HISTORICAL_MAX_CALLS_PER_MINUTE",
    "ENTRY_SCORE", "STRONG_ENTRY", "TELEGRAM_MIN_PROBABILITY",
    "OPTIONS_INTELLIGENCE_MIN_SCORE",
)

# Fields expected to be a fraction in [0, 1] -- named *_PCT / *_FRACTION
# by convention in this codebase, so a value outside that range is
# almost always a typo (e.g. "3" meant as "3%" i.e. 0.03).
_FRACTION_SUFFIXES = ("_PCT", "_FRACTION")

# _PCT fields that are legitimate exceptions to the >1-is-suspicious
# heuristic above, confirmed 2026-07-31 against each field's own
# inline comment in config.py -- not typos, just two different uses
# of the "_PCT" suffix that coexist in this codebase:
#   1. MOMENTUM_ROC_CEILING_PCT, REPLAY_CALIBRATION_GAP_THRESHOLD_PCT --
#      these use a raw PERCENTAGE-POINT scale (e.g. 1.5 literally means
#      "1.5%"), not the 0-1 fraction-of-1 scale this check assumes.
#   2. IV_ABNORMAL_HIGH_PCT -- DOES use the 0-1 fraction scale (1.20
#      means "120%"), but 1.20 is the intended value: its entire job
#      is to catch IV readings ABOVE 100%, so exceeding 1 is correct
#      behavior, not a mistake.
# If a genuinely new field needs adding here, confirm its own comment
# states the >1 value is intentional before adding it -- this list
# existing is not itself permission to silence a real typo.
_FRACTION_FIELD_ALLOWLIST = (
    "MOMENTUM_ROC_CEILING_PCT",
    "REPLAY_CALIBRATION_GAP_THRESHOLD_PCT",
    "IV_ABNORMAL_HIGH_PCT",
    "TRAILING_STOP_TRIGGER_PCT",  # 2026-07-31: same raw-percentage-point convention (3.0 means "3%"),
                                  # see its own comment in config.py
)

# Symbol-list fields checked for internal duplicates.
_SYMBOL_LIST_FIELDS = ("SYMBOLS", "STOCK_SYMBOLS", "INDEX_SYMBOLS")

# (hour, minute) tuple fields checked for a legal 24h clock value.
_HHMM_FIELDS = ("MARKET_OPEN_HHMM", "MARKET_CLOSE_HHMM", "MARKET_CLOSE_HHMM_HEARTBEAT", "SUMMARY_HHMM")

# Dict fields whose values must all be positive (per-symbol strike steps etc.)
_POSITIVE_VALUED_DICTS = ("STRIKE_STEP", "STOCK_STRIKE_STEP")


def _get(cfg: Any, name: str) -> Optional[Any]:
    return getattr(cfg, name, None)


def _check_positive_fields(cfg: Any, report: ConfigValidationReport) -> None:
    for name in _MUST_BE_POSITIVE:
        value = _get(cfg, name)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if value <= 0:
            report.add(Severity.ERROR, name, f"must be positive, got {value!r}.")


def _check_fraction_fields(cfg: Any, report: ConfigValidationReport) -> None:
    for name in dir(cfg):
        if not name.endswith(_FRACTION_SUFFIXES):
            continue
        if name in _FRACTION_FIELD_ALLOWLIST:
            continue
        value = _get(cfg, name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if value < 0:
            report.add(Severity.ERROR, name, f"fractional threshold is negative ({value!r}).")
        elif value > 1:
            report.add(
                Severity.WARNING, name,
                f"fractional threshold {value!r} is >1 -- confirm this is meant as a "
                "raw multiple, not a mistyped percentage (e.g. 3 instead of 0.03).",
            )


def _check_duplicate_symbols(cfg: Any, report: ConfigValidationReport) -> None:
    for name in _SYMBOL_LIST_FIELDS:
        symbols = _get(cfg, name)
        if not isinstance(symbols, list):
            continue
        seen = set()
        dupes = set()
        for s in symbols:
            if s in seen:
                dupes.add(s)
            seen.add(s)
        if dupes:
            report.add(
                Severity.ERROR, name,
                f"duplicate symbol(s) {sorted(dupes)} -- would double-count in scans/alerts.",
            )


def _check_hhmm_fields(cfg: Any, report: ConfigValidationReport) -> None:
    for name in _HHMM_FIELDS:
        value = _get(cfg, name)
        if value is None:
            continue
        if not (isinstance(value, tuple) and len(value) == 2):
            report.add(Severity.ERROR, name, f"expected an (hour, minute) tuple, got {value!r}.")
            continue
        hour, minute = value
        if not (0 <= hour <= 23) or not (0 <= minute <= 59):
            report.add(Severity.ERROR, name, f"invalid clock time {value!r} (hour 0-23, minute 0-59).")

    open_hhmm = _get(cfg, "MARKET_OPEN_HHMM")
    close_hhmm = _get(cfg, "MARKET_CLOSE_HHMM")
    if isinstance(open_hhmm, tuple) and isinstance(close_hhmm, tuple) and open_hhmm >= close_hhmm:
        report.add(
            Severity.ERROR, "MARKET_OPEN_HHMM/MARKET_CLOSE_HHMM",
            f"market open {open_hhmm} is not before market close {close_hhmm}.",
        )


def _check_positive_valued_dicts(cfg: Any, report: ConfigValidationReport) -> None:
    for name in _POSITIVE_VALUED_DICTS:
        d = _get(cfg, name)
        if not isinstance(d, dict):
            continue
        for key, value in d.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                report.add(Severity.ERROR, f"{name}[{key!r}]", f"strike step must be positive, got {value!r}.")


def _check_recommendations(cfg: Any, report: ConfigValidationReport) -> None:
    """Non-blocking nudges -- never an ERROR or WARNING, just surfaced
    for visibility in the production report (item 14)."""
    entry_score = _get(cfg, "ENTRY_SCORE")
    strong_entry = _get(cfg, "STRONG_ENTRY")
    if isinstance(entry_score, (int, float)) and isinstance(strong_entry, (int, float)):
        if strong_entry <= entry_score:
            report.add(
                Severity.WARNING, "STRONG_ENTRY",
                f"STRONG_ENTRY ({strong_entry}) should be greater than ENTRY_SCORE ({entry_score}).",
            )

    if _get(cfg, "STOCK_SYMBOLS") and not _get(cfg, "USE_DYNAMIC_FNO_UNIVERSE"):
        report.add(
            Severity.RECOMMENDATION, "USE_DYNAMIC_FNO_UNIVERSE",
            "STOCK_SYMBOLS is populated but USE_DYNAMIC_FNO_UNIVERSE is False -- "
            "the static FALLBACK_STOCK_WATCHLIST will be used instead of the live F&O universe.",
        )


def validate_config(cfg: Any = None) -> ConfigValidationReport:
    """Runs every check against `cfg` (defaults to importing config.py)
    and returns a report. Never raises; never mutates `cfg`."""
    if cfg is None:
        import config as cfg  # local import: keeps this module importable
        # even in contexts (e.g. tests) that construct a fake config object.

    report = ConfigValidationReport()
    _check_positive_fields(cfg, report)
    _check_fraction_fields(cfg, report)
    _check_duplicate_symbols(cfg, report)
    _check_hhmm_fields(cfg, report)
    _check_positive_valued_dicts(cfg, report)
    _check_recommendations(cfg, report)
    return report
