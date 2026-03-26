"""
dedup.py
SQLite-backed deduplication store.

Tracks every email ID that has been successfully processed (routed or queued).
If the app crashes mid-batch and restarts, emails already handled won't be
processed a second time.

Why SQLite and not a plain text file:
  - Atomic writes — no partial state if the process is killed mid-write
  - Concurrent-safe reads (WAL mode)
  - Fast indexed lookups even with tens of thousands of records
  - No external service required
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DB_PATH = "processed_emails.db"


class DeduplicationStore:

    def __init__(self, db_path: str = DB_PATH) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self._path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")   # safe concurrent access
        con.execute("PRAGMA synchronous=NORMAL") # balance safety vs speed
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_db(self) -> None:
        with self._conn() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS processed_emails (
                    email_id    TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL,
                    category    TEXT,
                    destination TEXT
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_processed_at
                ON processed_emails (processed_at)
            """)
        log.debug("Deduplication store ready at %s", self._path)

    def is_processed(self, email_id: str) -> bool:
        """Return True if this email ID has already been handled."""
        with self._conn() as con:
            row = con.execute(
                "SELECT 1 FROM processed_emails WHERE email_id = ?", (email_id,)
            ).fetchone()
        return row is not None

    def mark_processed(self, email_id: str, category: str = "", destination: str = "") -> None:
        """Record that this email has been handled. Safe to call multiple times."""
        with self._conn() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO processed_emails
                    (email_id, processed_at, category, destination)
                VALUES (?, ?, ?, ?)
                """,
                (
                    email_id,
                    datetime.now(timezone.utc).isoformat(),
                    category,
                    destination,
                ),
            )

    def count(self) -> int:
        """Return total number of processed emails — useful for metrics."""
        with self._conn() as con:
            row = con.execute("SELECT COUNT(*) FROM processed_emails").fetchone()
        return row[0] if row else 0

    def prune(self, keep_days: int = 90) -> int:
        """
        Delete records older than keep_days to prevent unbounded growth.
        Returns the number of rows deleted.
        Call this periodically (e.g. once a day from main.py).
        """
        with self._conn() as con:
            cur = con.execute(
                """
                DELETE FROM processed_emails
                WHERE processed_at < datetime('now', ? || ' days')
                """,
                (f"-{keep_days}",),
            )
        deleted = cur.rowcount
        if deleted:
            log.info("Pruned %d old dedup records (older than %d days).", deleted, keep_days)
        return deleted
