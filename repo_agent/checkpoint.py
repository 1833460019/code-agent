from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class CheckpointStore:
    """SQLite run checkpoints with a renewable single-writer lease."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                "lease_until REAL NOT NULL, status TEXT NOT NULL, state TEXT, updated_at REAL NOT NULL)"
            )

    def acquire(self, run_id: str, owner: str, *, resume: bool, lease_seconds: float) -> dict | None:
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT owner, lease_until, state FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row and row[1] > now and row[0] != owner:
                raise RuntimeError(f"Run {run_id!r} already has an active lease")
            state = json.loads(row[2]) if resume and row and row[2] else None
            db.execute(
                "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, owner, now + lease_seconds, "running", row[2] if state else None, now),
            )
            return state

    def save(self, run_id: str, owner: str, state: dict, *, lease_seconds: float) -> None:
        now = time.time()
        with self._connect() as db:
            changed = db.execute(
                "UPDATE runs SET state = ?, lease_until = ?, updated_at = ? "
                "WHERE run_id = ? AND owner = ?",
                (json.dumps(state, ensure_ascii=False), now + lease_seconds, now, run_id, owner),
            ).rowcount
            if changed != 1:
                raise RuntimeError("Run checkpoint lease was lost")

    def complete(self, run_id: str, owner: str, status: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE runs SET status = ?, lease_until = 0, updated_at = ? "
                "WHERE run_id = ? AND owner = ?",
                (status, time.time(), run_id, owner),
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        finally:
            db.close()
