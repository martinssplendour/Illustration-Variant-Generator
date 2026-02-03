"""Initializes the database schema and loads styles from local assets."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg import conninfo
from dotenv import load_dotenv
from PyPDF2 import PdfReader


STYLE_GUIDES_DIR = Path("style_guides")
STYLE_IMAGES_DIR = Path("style_images")
CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))

STYLE_ENTRIES = [
    {
        "style_id": "ks1",
        "style_name": "KS1 style",
        "guide": "KS1 style.pdf",
        "image": "ks1 image (1).png",
    },
    {
        "style_id": "ks2",
        "style_name": "KS2 style",
        "guide": "KS2 style.pdf",
        "image": "ks2.png",
    },
    {
        "style_id": "phonics",
        "style_name": "Phonics style",
        "guide": "Phonics style.pdf",
        "image": "phonics_downscaled.png",
    },
]

MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = " ".join(text.split())
        if text:
            parts.append(text)
    return "\n".join(parts)


def _extract_pdf_json(text: str) -> object | None:
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    while idx < length:
        if text[idx] not in "{[":
            idx += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, idx)
        except Exception:
            idx += 1
            continue
        return obj
    return None


def _strip_profile_keys(profile: object | None) -> object | None:
    if profile is None:
        return None
    if isinstance(profile, dict):
        cleaned: dict = {}
        for key, value in profile.items():
            if isinstance(key, str) and "cheek" in key.lower():
                continue
            cleaned[key] = _strip_profile_keys(value)
        return cleaned
    if isinstance(profile, list):
        return [_strip_profile_keys(item) for item in profile]
    return profile


def _ensure_database(db_url: str) -> None:
    info = conninfo.conninfo_to_dict(db_url)
    dbname = info.get("dbname")
    if not dbname:
        raise RuntimeError("DATABASE_URL missing dbname.")

    maintenance = dict(info)
    maintenance["dbname"] = "postgres"

    # Connect to the postgres maintenance DB to create the target DB if missing.
    with psycopg.connect(
        **maintenance,
        autocommit=True,
        connect_timeout=CONNECT_TIMEOUT,
    ) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (dbname,),
        ).fetchone()
        if not row:
            conn.execute(f'CREATE DATABASE "{dbname}"')


def _ensure_schema(conn: psycopg.Connection) -> None:
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


def _load_style_entry(entry: dict) -> tuple[str, str, str, bytes, str, str | None]:
    guide_path = STYLE_GUIDES_DIR / entry["guide"]
    image_path = STYLE_IMAGES_DIR / entry["image"]

    # Style rules come from PDFs; reference images are stored as bytes.
    if not guide_path.is_file():
        raise FileNotFoundError(f"Missing guide: {guide_path}")
    if not image_path.is_file():
        raise FileNotFoundError(f"Missing image: {image_path}")

    rules_text = _extract_pdf_text(guide_path)
    style_profile = _strip_profile_keys(_extract_pdf_json(rules_text))
    style_profile_json = (
        json.dumps(style_profile, ensure_ascii=True) if style_profile is not None else None
    )
    image_bytes = image_path.read_bytes()
    suffix = image_path.suffix.lower()
    mime = MIME_BY_SUFFIX.get(suffix, "application/octet-stream")
    if mime == "application/octet-stream":
        raise ValueError(f"Unsupported image type for {image_path}")

    return (
        entry["style_id"],
        entry["style_name"],
        rules_text,
        image_bytes,
        mime,
        style_profile_json,
    )


def main() -> int:
    load_dotenv()
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1

    _ensure_database(db_url)

    with psycopg.connect(db_url, connect_timeout=CONNECT_TIMEOUT) as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM styles")
        for entry in STYLE_ENTRIES:
            style_id, style_name, rules_text, image_bytes, mime, style_profile = (
                _load_style_entry(entry)
            )
            conn.execute(
                """
                INSERT INTO styles
                    (style_id, style_name, rules_text, reference_image, reference_mime, style_profile)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (style_id)
                DO UPDATE SET
                    style_name = EXCLUDED.style_name,
                    rules_text = EXCLUDED.rules_text,
                    reference_image = EXCLUDED.reference_image,
                    reference_mime = EXCLUDED.reference_mime,
                    style_profile = EXCLUDED.style_profile
                """,
                (style_id, style_name, rules_text, image_bytes, mime, style_profile),
            )

    print("Database initialized and styles loaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
