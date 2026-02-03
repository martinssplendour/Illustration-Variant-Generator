"""Generation history persistence and retrieval in Postgres."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

import logging

from .timing import log_timing

DEFAULT_HISTORY_LIMIT = 200

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryEntry:
    entry_id: str
    result_id: str
    original_url: Optional[str]
    created_at: datetime


class GenerationHistoryStore:
    """Stores and retrieves per-session generation history in Postgres."""

    def __init__(self, dsn: str, max_entries: int = DEFAULT_HISTORY_LIMIT) -> None:
        self._dsn = dsn
        self._max_entries = max_entries

    def ensure_schema(self) -> None:
        """Creates the generation_history table and index if they do not exist."""
        with log_timing("db generation_history ensure_schema", logger):
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS generation_history (
                        id UUID PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        result_id UUID NOT NULL,
                        original_url TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS generation_history_session_idx
                    ON generation_history (session_id, created_at)
                    """
                )

    def add_entry(self, session_id: str, result_id: str, original_url: str | None) -> None:
        """Records one generation output and keeps the newest entries per session."""
        result_uuid = _coerce_uuid(result_id)
        if not result_uuid:
            raise ValueError("Invalid result id for history entry.")

        entry_id = uuid4()
        with log_timing("db generation_history add_entry", logger):
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    """
                    INSERT INTO generation_history (id, session_id, result_id, original_url)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (entry_id, session_id, result_uuid, original_url),
                )
                if self._max_entries > 0:
                    conn.execute(
                        """
                        DELETE FROM generation_history
                        WHERE id IN (
                            SELECT id FROM generation_history
                            WHERE session_id = %s
                            ORDER BY created_at DESC
                            OFFSET %s
                        )
                        """,
                        (session_id, self._max_entries),
                    )

    def list_entries(self, session_id: str, limit: int | None = None) -> list[HistoryEntry]:
        """Returns the most recent history entries for the given session."""
        fetch_limit = self._max_entries if limit is None else limit
        with log_timing("db generation_history list_entries", logger):
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                rows = conn.execute(
                    """
                    SELECT id, result_id, original_url, created_at
                    FROM generation_history
                    WHERE session_id = %s
                    ORDER BY created_at ASC
                    LIMIT %s
                    """,
                    (session_id, fetch_limit),
                ).fetchall()

        return [
            HistoryEntry(
                entry_id=str(row["id"]),
                result_id=str(row["result_id"]),
                original_url=row["original_url"],
                created_at=row["created_at"],
            )
            for row in rows
        ]


def _coerce_uuid(value: str) -> Optional[UUID]:
    """Parses a UUID or returns None when the value is invalid."""
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None
