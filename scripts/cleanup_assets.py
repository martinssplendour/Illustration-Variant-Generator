"""Prunes expired assets and history records from Postgres."""

from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _interval(days: int) -> str:
    return f"{days} days"


def main() -> int:
    load_dotenv()
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1

    # TTLs are read from the environment to control retention policy.
    upload_ttl = _get_int("ASSET_TTL_UPLOAD_DAYS", 14)
    result_ttl = _get_int("ASSET_TTL_RESULT_DAYS", 30)
    bg_ttl = _get_int("ASSET_TTL_BG_REMOVED_DAYS", 30)
    grace_days = _get_int("ASSET_GRACE_DAYS", 7)
    history_ttl = _get_int("HISTORY_TTL_DAYS", 90)

    with psycopg.connect(db_url, autocommit=True) as conn:
        # Ensure retention columns exist for older databases.
        conn.execute("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS last_accessed TIMESTAMPTZ")
        conn.execute("ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
        conn.execute(
            "ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE"
        )

        if history_ttl > 0:
            # Remove old history rows outright.
            conn.execute(
                "DELETE FROM generation_history WHERE created_at < NOW() - (%s::interval)",
                (_interval(history_ttl),),
            )

        if upload_ttl > 0:
            conn.execute(
                """
                UPDATE image_assets
                SET deleted_at = NOW()
                WHERE deleted_at IS NULL
                  AND pinned IS NOT TRUE
                  AND role = 'upload'
                  AND created_at < NOW() - (%s::interval)
                """,
                (_interval(upload_ttl),),
            )

        if result_ttl > 0:
            conn.execute(
                """
                UPDATE image_assets
                SET deleted_at = NOW()
                WHERE deleted_at IS NULL
                  AND pinned IS NOT TRUE
                  AND role = 'result'
                  AND created_at < NOW() - (%s::interval)
                  AND NOT EXISTS (
                      SELECT 1 FROM generation_history gh
                      WHERE gh.result_id = image_assets.id
                  )
                """,
                (_interval(result_ttl),),
            )

        if bg_ttl > 0:
            conn.execute(
                """
                UPDATE image_assets
                SET deleted_at = NOW()
                WHERE deleted_at IS NULL
                  AND pinned IS NOT TRUE
                  AND role = 'bg_removed'
                  AND created_at < NOW() - (%s::interval)
                  AND NOT EXISTS (
                      SELECT 1 FROM generation_history gh
                      WHERE gh.result_id = image_assets.id
                  )
                """,
                (_interval(bg_ttl),),
            )

        if grace_days > 0:
            conn.execute(
                """
                DELETE FROM image_assets
                WHERE deleted_at IS NOT NULL
                  AND deleted_at < NOW() - (%s::interval)
                """,
                (_interval(grace_days),),
            )

    print("Retention cleanup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
