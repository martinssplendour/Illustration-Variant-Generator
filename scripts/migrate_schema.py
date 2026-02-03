"""Applies schema migrations to Postgres."""

from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1

    # Idempotent schema creation for existing databases.
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS image_assets (
                id UUID PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                filename TEXT,
                content_type TEXT NOT NULL,
                image_bytes BYTEA NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_accessed TIMESTAMPTZ,
                deleted_at TIMESTAMPTZ,
                pinned BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS image_assets_session_idx
            ON image_assets (session_id)
            """
        )
        conn.execute("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS last_accessed TIMESTAMPTZ")
        conn.execute("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
        conn.execute(
            "ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE"
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS image_assets_cleanup_idx
            ON image_assets (deleted_at, role, created_at)
            """
        )

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

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS styles (
                style_id TEXT PRIMARY KEY,
                style_name TEXT NOT NULL,
                rules_text TEXT NOT NULL,
                reference_image BYTEA NOT NULL,
                reference_mime TEXT NOT NULL,
                style_profile TEXT
            )
            """
        )
        conn.execute("ALTER TABLE styles ADD COLUMN IF NOT EXISTS style_profile TEXT")

    print("Schema migration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
