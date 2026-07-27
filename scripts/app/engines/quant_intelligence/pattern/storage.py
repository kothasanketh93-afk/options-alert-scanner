"""
pattern/storage.py -- Local-only historical pattern database.

Uses stdlib sqlite3 (no cloud, no external DB server, per the spec).
Thread-safe via a module-level lock around writes; sqlite3 connections
are opened per-call with `check_same_thread=False` since the host
scanner may call from a worker thread pool.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from ..utils.exceptions import StorageError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pattern_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_key TEXT NOT NULL,
    symbol TEXT,
    direction TEXT,
    outcome TEXT NOT NULL,          -- 'WIN' | 'LOSS' | 'BREAKEVEN'
    target_pct REAL,                -- realized move to exit, % of entry
    sl_pct REAL,                    -- realized adverse move if lost, % of entry
    recorded_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pattern_key ON pattern_outcomes (pattern_key);
"""


class PatternStorage:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=5.0)
        try:
            yield conn
        except sqlite3.Error as exc:
            raise StorageError(f"Pattern storage error: {exc}", context={"db_path": self._db_path}) from exc
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def record_outcome(
        self,
        pattern_key: str,
        symbol: str,
        direction: str,
        outcome: str,
        *,
        target_pct: Optional[float] = None,
        sl_pct: Optional[float] = None,
        recorded_at: Optional[float] = None,
    ) -> None:
        import time

        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO pattern_outcomes "
                "(pattern_key, symbol, direction, outcome, target_pct, sl_pct, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    pattern_key,
                    symbol,
                    direction,
                    outcome,
                    target_pct,
                    sl_pct,
                    recorded_at if recorded_at is not None else time.time(),
                ),
            )
            conn.commit()

    def fetch_outcomes(self, pattern_key: str) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM pattern_outcomes WHERE pattern_key = ? ORDER BY recorded_at DESC",
                (pattern_key,),
            ).fetchall()
            return [dict(row) for row in rows]

    def top_similar_keys(self, prefix: str, limit: int = 5) -> List[str]:
        """Loose similarity: patterns sharing the same coarse prefix
        (bucketized trend/regime portion of the key)."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT pattern_key FROM pattern_outcomes WHERE pattern_key LIKE ? LIMIT ?",
                (f"{prefix}%", limit),
            ).fetchall()
            return [r[0] for r in rows]

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM pattern_outcomes").fetchone()[0]
