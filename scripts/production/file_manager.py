"""
production/file_manager.py
-----------------------------
Part 9, item 11: File Management.

Generic, reusable helpers for the "create folders automatically,
delete expired files, archive old reports" lifecycle that today is
handled ad hoc in a few different places (logging_manager.py's own
retention sweep is one instance of this same pattern, kept separate
there since it also needs to know about gzip-rotated log-specific
naming). This module is the general-purpose version for anything
else: replay output, generated reports, cache files, temp files.

Every function here is pure filesystem manipulation with no knowledge
of what a "replay" or a "report" actually is -- callers pass a
directory + pattern + age threshold. Nothing existing has to change to
get this; it's opt-in utility functions, not a service that runs on
its own (wire calls to these into production.task_manager for periodic
sweeps, or call them directly, e.g. once at startup).
"""

from __future__ import annotations

import gzip
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class CleanupResult:
    directory: str
    pattern: str
    deleted: List[str]
    bytes_freed: int


@dataclass
class ArchiveResult:
    source_dir: str
    archive_dir: str
    archived: List[str]


def ensure_directories(paths: List[str]) -> List[str]:
    """Creates every path (including parents) if it doesn't already
    exist. Returns the list actually created (already-existing paths
    are silently skipped, not an error)."""
    created = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(str(path))
    return created


def delete_expired_files(directory: str, pattern: str = "*", max_age_days: float = 30.0) -> CleanupResult:
    """Deletes every file under `directory` matching `pattern` (glob,
    non-recursive by default -- pass e.g. "**/*.tmp" for recursive)
    whose modification time is older than max_age_days. Directories
    themselves are never deleted, only files."""
    path = Path(directory)
    deleted: List[str] = []
    bytes_freed = 0
    if not path.exists():
        return CleanupResult(directory=directory, pattern=pattern, deleted=deleted, bytes_freed=0)

    cutoff = time.time() - max_age_days * 86400
    for f in path.glob(pattern):
        if not f.is_file():
            continue
        try:
            if f.stat().st_mtime < cutoff:
                size = f.stat().st_size
                f.unlink()
                deleted.append(str(f))
                bytes_freed += size
        except OSError:
            continue  # a file that vanished/changed mid-sweep is not a cleanup failure
    return CleanupResult(directory=directory, pattern=pattern, deleted=deleted, bytes_freed=bytes_freed)


def cleanup_temp_files(directory: str, pattern: str = "*.tmp", max_age_hours: float = 24.0) -> CleanupResult:
    """Same as delete_expired_files but expressed in hours, since temp
    files (unlike logs/reports/replay output) are usually meant to
    live for at most a few hours, not days."""
    return delete_expired_files(directory, pattern=pattern, max_age_days=max_age_hours / 24.0)


def archive_old_reports(source_dir: str, archive_dir: str, pattern: str = "*",
                         max_age_days: float = 14.0, compress: bool = True) -> ArchiveResult:
    """Moves (optionally gzip-compressing) every file under
    `source_dir` matching `pattern` older than max_age_days into
    `archive_dir`, preserving the original filename (plus a .gz
    suffix if compressed). Creates archive_dir if it doesn't exist.
    A file already ending in .gz is moved as-is, never double-compressed."""
    src = Path(source_dir)
    dst = Path(archive_dir)
    archived: List[str] = []
    if not src.exists():
        return ArchiveResult(source_dir=source_dir, archive_dir=archive_dir, archived=archived)

    dst.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - max_age_days * 86400
    for f in src.glob(pattern):
        if not f.is_file():
            continue
        try:
            if f.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue

        if compress and not f.name.endswith(".gz"):
            target = dst / f"{f.name}.gz"
            with open(f, "rb") as src_fh, gzip.open(target, "wb") as dst_fh:
                shutil.copyfileobj(src_fh, dst_fh)
            f.unlink()
        else:
            target = dst / f.name
            shutil.move(str(f), str(target))
        archived.append(str(target))
    return ArchiveResult(source_dir=source_dir, archive_dir=archive_dir, archived=archived)


def directory_size_bytes(directory: str) -> int:
    """Recursive total size of every file under `directory`. Used to
    feed a "disk used by <category>" number into the production report
    (item 14) without relying on shutil.disk_usage, which reports
    whole-filesystem usage, not per-directory."""
    path = Path(directory)
    if not path.exists():
        return 0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                continue
    return total
