"""
learning/history.py -- Append-only local trade history.

Stores one JSON line per recorded trade outcome: the original signal
snapshot, the probability/risk/regime/pattern results at decision time,
and the final realized outcome (SL hit / target hit / exit / PnL /
duration). JSONL keeps writes O(1) and crash-safe (each line is
independent), and avoids needing a second SQLite table for this.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from ..utils.exceptions import StorageError


class TradeHistoryStore:
    def __init__(self, path: str, max_records: int = 20000) -> None:
        self._path = path
        self._max_records = max_records
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if not os.path.isfile(path):
            open(path, "a", encoding="utf-8").close()

    def append(self, record: Dict[str, Any]) -> None:
        record = dict(record)
        record.setdefault("recorded_at", time.time())
        with self._lock:
            try:
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, default=str) + "\n")
            except OSError as exc:
                raise StorageError(f"Failed to append trade history: {exc}", context={"path": self._path}) from exc
            self._maybe_truncate_locked()

    def _maybe_truncate_locked(self) -> None:
        # Cheap periodic truncation: only check every so often by sampling
        # file size rather than counting lines on every single append.
        try:
            if os.path.getsize(self._path) < 2_000_000:  # ~2MB heuristic before we bother counting
                return
            with open(self._path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            if len(lines) > self._max_records:
                trimmed = lines[-self._max_records :]
                with open(self._path, "w", encoding="utf-8") as fh:
                    fh.writelines(trimmed)
        except OSError as exc:
            raise StorageError(f"Failed to truncate trade history: {exc}", context={"path": self._path}) from exc

    def recent(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
            except OSError as exc:
                raise StorageError(f"Failed to read trade history: {exc}", context={"path": self._path}) from exc
        out = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
