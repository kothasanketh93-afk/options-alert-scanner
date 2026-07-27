"""
production/production_report.py
-----------------------------------
Part 9, item 14: Production Report.

The capstone: pulls together everything Phases 1-6 built (config
validation, startup validation, extended health, resource monitoring,
background tasks, cache stats) into one report with issues bucketed by
severity, summaries per subsystem, a single readiness percentage, and
concrete recommendations -- meant to be generated once at startup (log
it, don't just print it) and optionally on a schedule via
production.task_manager for a long-running process.

Every input is optional (same decoupled pattern as health_aggregator
and resource_monitor) -- call generate_production_report() with
whatever you have available; anything not supplied is reported as
"not assessed" rather than skipped silently, so a reader can see what
this run did and didn't check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from production.config_validator import ConfigValidationReport, Severity as ConfigSeverity
from production.startup_validator import StartupReport, CheckStatus


class IssueSeverity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ProductionIssue:
    severity: IssueSeverity
    source: str
    message: str


@dataclass
class ProductionReport:
    issues: List[ProductionIssue] = field(default_factory=list)
    performance_summary: Dict[str, Any] = field(default_factory=dict)
    memory_summary: Dict[str, Any] = field(default_factory=dict)
    api_summary: Dict[str, Any] = field(default_factory=dict)
    thread_summary: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    sections_assessed: List[str] = field(default_factory=list)

    def by_severity(self, severity: IssueSeverity) -> List[ProductionIssue]:
        return [i for i in self.issues if i.severity is severity]

    @property
    def readiness_percent(self) -> float:
        """100, minus a fixed deduction per issue by severity, floored
        at 0. A transparent checklist (same style as health_service.py
        and health_aggregator.py's scoring), not a statistical model:
        CRITICAL -25, HIGH -10, MEDIUM -4, LOW -1 each."""
        deductions = (
            25 * len(self.by_severity(IssueSeverity.CRITICAL))
            + 10 * len(self.by_severity(IssueSeverity.HIGH))
            + 4 * len(self.by_severity(IssueSeverity.MEDIUM))
            + 1 * len(self.by_severity(IssueSeverity.LOW))
        )
        return max(0.0, 100.0 - deductions)

    def render(self) -> str:
        lines = ["=" * 70, "PRODUCTION READINESS REPORT", "=" * 70]
        lines.append(f"Readiness: {self.readiness_percent:.0f}%")
        lines.append(f"Sections assessed: {', '.join(self.sections_assessed) or 'none'}")
        lines.append("")
        for sev in (IssueSeverity.CRITICAL, IssueSeverity.HIGH, IssueSeverity.MEDIUM, IssueSeverity.LOW):
            group = self.by_severity(sev)
            lines.append(f"{sev.value.upper()} ({len(group)})")
            for issue in group:
                lines.append(f"  - [{issue.source}] {issue.message}")
            if not group:
                lines.append("  (none)")
        lines.append("")
        lines.append(f"Performance summary: {self.performance_summary or '(not assessed)'}")
        lines.append(f"Memory summary: {self.memory_summary or '(not assessed)'}")
        lines.append(f"API summary: {self.api_summary or '(not assessed)'}")
        lines.append(f"Thread summary: {self.thread_summary or '(not assessed)'}")
        lines.append("")
        lines.append("Recommendations:")
        for rec in self.recommendations:
            lines.append(f"  - {rec}")
        if not self.recommendations:
            lines.append("  (none)")
        lines.append("=" * 70)
        return "\n".join(lines)


def _from_config_report(report: Optional[ConfigValidationReport], issues: List[ProductionIssue],
                         recs: List[str], sections: List[str]) -> None:
    if report is None:
        return
    sections.append("config_validation")
    for i in report.errors:
        issues.append(ProductionIssue(IssueSeverity.HIGH, "config", f"{i.field}: {i.message}"))
    for i in report.warnings:
        issues.append(ProductionIssue(IssueSeverity.MEDIUM, "config", f"{i.field}: {i.message}"))
    for i in report.recommendations:
        recs.append(f"[config] {i.field}: {i.message}")


def _from_startup_report(report: Optional[StartupReport], issues: List[ProductionIssue],
                          sections: List[str]) -> None:
    if report is None:
        return
    sections.append("startup_validation")
    for c in report.checks:
        if c.status is CheckStatus.CRITICAL:
            issues.append(ProductionIssue(IssueSeverity.CRITICAL, "startup", f"{c.name}: {c.message}"))
        elif c.status is CheckStatus.WARNING:
            issues.append(ProductionIssue(IssueSeverity.LOW, "startup", f"{c.name}: {c.message}"))


def _from_extended_health(health: Optional[Dict[str, Any]], issues: List[ProductionIssue],
                           memory_summary: Dict[str, Any], thread_summary: Dict[str, Any],
                           sections: List[str]) -> None:
    if health is None:
        return
    sections.append("health")

    resources = health.get("resources", {})
    if resources.get("status") == "ok":
        memory_summary.update({
            "memory_pct": resources.get("memory_pct"),
            "cpu_pct": resources.get("cpu_pct"),
            "disk_pct": resources.get("disk_pct"),
        })
        thread_summary["thread_count"] = resources.get("thread_count")
        for alert in resources.get("alerts", []):
            severity = IssueSeverity.HIGH if alert["level"] == "critical" else IssueSeverity.MEDIUM
            issues.append(ProductionIssue(severity, "resources", alert["message"]))

    tasks = health.get("background_tasks", {})
    if tasks.get("status") == "ok":
        thread_summary["running_workers"] = tasks.get("running_count")
        for w in tasks.get("workers", []):
            if w["state"] == "crashed":
                issues.append(ProductionIssue(IssueSeverity.HIGH, "background_tasks",
                                               f"worker '{w['name']}' crashed: {w.get('last_error')}"))
        if tasks.get("unhealthy_count", 0) > 0:
            issues.append(ProductionIssue(IssueSeverity.MEDIUM, "background_tasks",
                                           f"{tasks['unhealthy_count']} worker(s) missed heartbeat."))

    for component_name in ("broker", "telegram", "api", "websocket"):
        component = health.get(component_name, {})
        if component.get("status") == "error":
            issues.append(ProductionIssue(IssueSeverity.HIGH, component_name,
                                           component.get("error", "unknown error")))

    base = health.get("base", {})
    for issue_msg in base.get("issues", []):
        issues.append(ProductionIssue(IssueSeverity.MEDIUM, "base_health", issue_msg))


def _from_cache_stats(cache_stats: Optional[Dict[str, int]], api_summary: Dict[str, Any],
                       recs: List[str], sections: List[str]) -> None:
    if cache_stats is None:
        return
    sections.append("cache")
    total = cache_stats.get("hits", 0) + cache_stats.get("misses", 0)
    hit_ratio = (cache_stats["hits"] / total) if total else None
    api_summary["cache_entries"] = cache_stats.get("entries")
    api_summary["cache_hit_ratio"] = round(hit_ratio, 3) if hit_ratio is not None else None
    if hit_ratio is not None and hit_ratio < 0.3 and total >= 20:
        recs.append(
            f"[cache] hit ratio is low ({hit_ratio:.0%} over {total} lookups) -- "
            "confirm the TTL matches how often the underlying data actually changes."
        )


def generate_production_report(
    config_report: Optional[ConfigValidationReport] = None,
    startup_report: Optional[StartupReport] = None,
    extended_health: Optional[Dict[str, Any]] = None,
    cache_stats: Optional[Dict[str, int]] = None,
    performance_summary: Optional[Dict[str, Any]] = None,
) -> ProductionReport:
    """Combines whatever subset of reports you pass in. Typical
    startup call site (main.py, after run_startup_validation() and
    before the first scan cycle):

        cfg_report = config_validator.validate_config()
        report = production_report.generate_production_report(
            config_report=cfg_report,
            startup_report=startup_report,           # from run_startup_validation()
            extended_health=health_aggregator.get_extended_health(...),
        )
        logger.info(report.render())
    """
    issues: List[ProductionIssue] = []
    recommendations: List[str] = []
    sections: List[str] = []
    memory_summary: Dict[str, Any] = {}
    thread_summary: Dict[str, Any] = {}
    api_summary: Dict[str, Any] = {}

    _from_config_report(config_report, issues, recommendations, sections)
    _from_startup_report(startup_report, issues, sections)
    _from_extended_health(extended_health, issues, memory_summary, thread_summary, sections)
    _from_cache_stats(cache_stats, api_summary, recommendations, sections)

    if not sections:
        recommendations.append(
            "No subsystems were assessed -- pass at least a config_report or extended_health "
            "to generate_production_report() for a meaningful readiness score."
        )

    return ProductionReport(
        issues=issues,
        performance_summary=performance_summary or {},
        memory_summary=memory_summary,
        api_summary=api_summary,
        thread_summary=thread_summary,
        recommendations=recommendations,
        sections_assessed=sections,
    )
