"""
backfill_export.py
-------------------
One-off historical export for troubleshooting, added 2026-08-13.

WHY THIS EXISTS: scan_log_store.py's centralized, structured store only
has data from the day it was deployed onward -- it never backfills
runs that happened before it existed (see that module's own docstring).
But GitHub Actions already keeps its own raw logs for every past run
(typically ~90 days, per repo settings) -- this script pulls those
directly from the Actions API for a date range and concatenates them
into ONE combined text file, so past days' troubleshooting data isn't
lost just because the new structured store didn't exist yet when they
ran.

This is a SEPARATE, one-off tool -- it does not read from or write to
the `state` branch / scan_logs/ store at all, and is not part of the
automatic per-run path (same "only when explicitly requested" posture
as scan_log_store.export_logs()).

Requires a token with `actions: read` on this repo (the default
GITHUB_TOKEN works when the calling workflow grants that permission).
"""

import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime

import requests

API_ROOT = "https://api.github.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _list_workflows(repo: str, token: str) -> list:
    """Lists every workflow file registered in this repo -- used as a
    diagnostic when _list_runs() finds a 404 for the requested
    workflow_file, so the error message can show what names ARE
    actually available instead of just failing opaquely."""
    resp = requests.get(f"{API_ROOT}/repos/{repo}/actions/workflows", headers=_headers(token), timeout=30)
    resp.raise_for_status()
    return [(w["path"].rsplit("/", 1)[-1], w["name"], w["state"]) for w in resp.json().get("workflows", [])]


def _list_runs(repo: str, token: str, workflow_file: str, date_start: str, date_end: str) -> list:
    """
    Lists every run of `workflow_file` (e.g. "trader.yml") with a
    `created` timestamp between date_start and date_end (both
    "YYYY-MM-DD", inclusive, compared in the UTC dates GitHub itself
    uses for run timestamps -- IST troubleshooting dates and UTC run
    dates can differ by a few hours right at the boundary; close enough
    for a "give me roughly the last N days" backfill, not something
    this script tries to correct for precisely).

    Raises a clear, actionable error (listing the repo's actual
    workflow file names) if `workflow_file` doesn't match anything --
    a wrong filename here used to silently return an empty run list,
    which then silently produced a 0-byte export with no indication of
    why (confirmed as the actual cause of a real "completely empty
    file" report from this feature's first real use).
    """
    runs = []
    page = 1
    url = f"{API_ROOT}/repos/{repo}/actions/workflows/{workflow_file}/runs"
    while True:
        resp = requests.get(
            url, headers=_headers(token),
            params={"created": f"{date_start}..{date_end}", "per_page": 100, "page": page},
            timeout=30,
        )
        if resp.status_code == 404:
            try:
                available = _list_workflows(repo, token)
            except requests.RequestException:
                available = []
            names = ", ".join(f"{path!r} ({name})" for path, name, state in available) or "(could not list workflows)"
            raise RuntimeError(
                f"No workflow file named {workflow_file!r} found in {repo}. "
                f"Available workflow files: {names}. Re-run with --workflow-file set to "
                f"the correct one (the FILE name under .github/workflows/, not the display name)."
            )
        resp.raise_for_status()
        data = resp.json()
        total = data.get("total_count", 0)
        batch = data.get("workflow_runs", [])
        if page == 1:
            print(
                f"GitHub reports {total} total run(s) of {workflow_file!r} in "
                f"{date_start}..{date_end} (UTC)."
                + ("" if total else " -- if this is unexpectedly 0, double check the date "
                                     "range is UTC (not IST) and that the workflow actually "
                                     "ran on those dates.")
            )
        runs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return runs


def _fetch_run_log_text(repo: str, token: str, run_id: int) -> str:
    """
    Downloads one run's full log archive (a zip of per-job/per-step
    .txt files) and returns every file's content concatenated. Returns
    an empty string (not an exception) if logs are no longer available
    for this run (e.g. past GitHub's retention window) -- a gap here
    should show up as a visibly empty section in the combined output,
    not abort the whole backfill.
    """
    url = f"{API_ROOT}/repos/{repo}/actions/runs/{run_id}/logs"
    resp = requests.get(url, headers=_headers(token), timeout=60)
    if resp.status_code == 404:
        return ""
    resp.raise_for_status()
    parts = []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for name in sorted(zf.namelist()):
            if name.endswith(".txt"):
                parts.append(f"--- {name} ---\n{zf.read(name).decode('utf-8', errors='replace')}")
    return "\n".join(parts)


def backfill(repo: str, token: str, date_start: str, date_end: str,
             workflow_file: str = "trader.yml", underlying: str = None,
             output_path: str = "backfill_export.txt") -> int:
    """
    Returns the number of runs included. Writes the combined text to
    output_path -- each run's logs prefixed with a header line (run id,
    started_at, conclusion) so it's still clear which 5-minute cycle
    each block of output came from.
    """
    runs = _list_runs(repo, token, workflow_file, date_start, date_end)
    runs.sort(key=lambda r: r["created_at"])  # oldest first

    with open(output_path, "w") as out:
        if not runs:
            out.write(
                f"No runs of {workflow_file!r} found for {date_start}..{date_end} (UTC).\n"
                f"This is written here instead of leaving a 0-byte file so an empty result "
                f"is never silently indistinguishable from a script failure.\n"
            )
        for run in runs:
            text = _fetch_run_log_text(repo, token, run["id"])
            if underlying:
                # BUGFIX (2026-08-13, found while writing this feature's own
                # test): a plain substring check ("underlying in line") makes
                # "NIFTY" incorrectly ALSO match "BANKNIFTY"/"MIDCPNIFTY"/
                # "FINNIFTY" lines, since they all contain "NIFTY" as a
                # substring -- wrong for a filter meant to isolate ONE
                # underlying. Word-boundary regex instead: matches "NIFTY"
                # as its own token, not as part of a longer symbol name.
                pattern = re.compile(r"\b" + re.escape(underlying) + r"\b")
                text = "\n".join(line for line in text.split("\n") if pattern.search(line))
            out.write(
                f"\n{'=' * 80}\nRUN {run['id']} | started {run['created_at']} | "
                f"conclusion={run.get('conclusion')}\n{'=' * 80}\n"
            )
            out.write(text if text else "(no logs available for this run -- past retention window)\n")

    return len(runs)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill-export raw Trader workflow logs from GitHub Actions history.")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--date-start", required=True, help="2026-08-13 (inclusive)")
    parser.add_argument("--date-end", required=True, help="2026-08-14 (inclusive)")
    parser.add_argument("--workflow-file", default="trader.yml")
    parser.add_argument("--underlying", default=None, help="Only keep lines mentioning this symbol")
    parser.add_argument("--output", default="backfill_export.txt")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    count = backfill(
        args.repo, token, args.date_start, args.date_end,
        workflow_file=args.workflow_file, underlying=args.underlying, output_path=args.output,
    )
    print(f"Backfilled {count} runs into {args.output}")
