"""
weekly_report.py
-----------------
Reads logs/rolling_log.jsonl and sends a grade-vs-outcome report to
Telegram. Run weekly (Monday morning IST) via its own workflow.

This is the "is my grading actually predictive" check -- buckets
resolved trades by GRADE (A+/A/B+/B, not a numeric score bucket, since
V2.0 doesn't expose raw scores anywhere) and reports win rate per
grade. If B-grade trades are winning at a similar rate to A+ trades,
that's a direct signal to revisit example_quality_score()'s weights
(or your own scoring logic).
"""

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(__file__))

import state as state_mod
import rolling_log
import telegram_notify as tg

GRADE_ORDER = ["A+", "A", "B+", "B"]


def build_report(entries: list) -> str:
    if not entries:
        return "WEEKLY GRADE REPORT\n\nNo resolved trades in the last 7 days."

    resolved = [
        e for e in entries
        if e.get("final_status") in ("T2_HIT", "T1_THEN_SL", "SL_HIT", "T1_HIT_THEN_AMBIGUOUS")
        or e.get("status") in ("EOD_CLOSED", "AMBIGUOUS", "NOT_ENTERED")
    ]

    lines = ["WEEKLY GRADE REPORT\n"]
    lines.append(f"Resolved trades: {len(resolved)} (last 7 days)\n")

    by_grade = {}
    for e in resolved:
        by_grade.setdefault(e.get("grade", "N/A"), []).append(e)

    for grade in GRADE_ORDER:
        items = by_grade.get(grade, [])
        if not items:
            continue
        wins = sum(1 for e in items if e.get("final_status") == "T2_HIT")
        partial = sum(1 for e in items if e.get("final_status") == "T1_THEN_SL")
        losses = sum(1 for e in items if e.get("final_status") == "SL_HIT")
        other = len(items) - wins - partial - losses
        decided = wins + losses
        rate = f"{wins/decided*100:.1f}%" if decided else "N/A"
        lines.append(
            f"Grade {grade}: {len(items)} trades | "
            f"T2: {wins}  T1-then-SL: {partial}  SL: {losses}  Other: {other} | "
            f"Clean win rate: {rate}"
        )

    lines.append("\nBy moneyness:")
    by_moneyness = {}
    for e in resolved:
        by_moneyness.setdefault(e.get("moneyness", "ATM"), []).append(e)
    for m, items in by_moneyness.items():
        wins = sum(1 for e in items if e.get("final_status") == "T2_HIT")
        losses = sum(1 for e in items if e.get("final_status") == "SL_HIT")
        decided = wins + losses
        rate = f"{wins/decided*100:.1f}%" if decided else "N/A"
        lines.append(f"  {m}: {len(items)} trades, win rate {rate}")

    # The actual point of this report: is grade ordering reflected in win rate?
    grade_rates = []
    for grade in GRADE_ORDER:
        items = by_grade.get(grade, [])
        wins = sum(1 for e in items if e.get("final_status") == "T2_HIT")
        losses = sum(1 for e in items if e.get("final_status") == "SL_HIT")
        decided = wins + losses
        if decided:
            grade_rates.append((grade, wins / decided))
    if len(grade_rates) >= 2:
        is_ordered = all(grade_rates[i][1] >= grade_rates[i+1][1] for i in range(len(grade_rates)-1))
        lines.append(
            "\nGrade ordering check: "
            + ("Higher grades ARE winning more -- scoring looks predictive."
               if is_ordered else
               "Higher grades are NOT consistently winning more -- worth reviewing scoring weights.")
        )

    return "\n".join(lines)


def main():
    repo_url = os.environ["REPO_URL"]
    github_token = os.environ["GITHUB_TOKEN"]

    state_mod.ensure_state_branch_checked_out(repo_url, github_token)
    entries = rolling_log.read_rolling_log()

    now = state_mod.ist_now()
    cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [e for e in entries if e["date"] >= cutoff]

    report = build_report(recent)
    tg.send(report)


if __name__ == "__main__":
    main()
