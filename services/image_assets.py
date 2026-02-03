"""Postgres-backed image asset storage and retrieval."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable, Optional
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from .timing import log_timing

logger = logging.getLogger(__name__)

IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class StorageError(ValueError):
    pass


@dataclass(frozen=True)
class StoredUpload:
    asset_id: str
    original_name: str
    suffix: str
    content_type: str
    image_bytes: bytes


@dataclass(frozen=True)
class ImageAsset:
    asset_id: str
    content_type: str
    filename: Optional[str]
    image_bytes: bytes
    role: str


class ImageAssetStore:
    def __init__(self, dsn: str, allowed_extensions: Iterable[str]) -> None:
        self._dsn = dsn
        self._allowed = {ext.lower() for ext in allowed_extensions}

    def ensure_schema(self) -> None:
        with log_timing("db image_assets ensure_schema", logger):
            with psycopg.connect(self._dsn) as conn:
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
                conn.execute(
                    "ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS last_accessed TIMESTAMPTZ"
                )
                conn.execute(
                    "ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ"
                )
                conn.execute(
                    "ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE"
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS image_assets_cleanup_idx
                    ON image_assets (deleted_at, role, created_at)
                    """
                )

    def save_upload_bytes(
        self,
        filename: str,
        content_type: Optional[str],
        image_bytes: bytes,
        session_id: str,
    ) -> StoredUpload:
        # Validate file metadata and whitelist extensions before storage.
        if not filename:
            raise StorageError("Please select an image.")

        safe_name = _secure_filename(filename)
        suffix = _normalize_suffix(_suffix_from_name(safe_name))
        if not suffix or suffix.lstrip(".") not in self._allowed:
            raise StorageError("Unsupported file type. Use PNG, JPG, JPEG, GIF, or WEBP.")

        if not image_bytes:
            raise StorageError("Uploaded file is empty.")

        content_type = _resolve_content_type(suffix, content_type)
        asset_id = uuid4()
        self._insert_asset(
            asset_id=asset_id,
            session_id=session_id,
            role="upload",
            filename=safe_name,
            content_type=content_type,
            image_bytes=image_bytes,
        )
        return StoredUpload(
            asset_id=str(asset_id),
            original_name=safe_name,
            suffix=suffix,
            content_type=content_type,
            image_bytes=image_bytes,
        )

    def save_bytes(
        self,
        session_id: str,
        image_bytes: bytes,
        content_type: str,
        filename: Optional[str] = None,
        role: str = "result",
    ) -> str:
        asset_id = uuid4()
        self._insert_asset(
            asset_id=asset_id,
            session_id=session_id,
            role=role,
            filename=filename,
            content_type=content_type,
            image_bytes=image_bytes,
        )
        return str(asset_id)

    def get_asset(self, session_id: str, asset_id: str) -> Optional[ImageAsset]:
        asset_uuid = _coerce_uuid(asset_id)
        if not asset_uuid:
            return None

        with log_timing(f"db get_asset {asset_uuid}", logger):
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                row = conn.execute(
                    """
                    SELECT id, filename, content_type, image_bytes, role
                    FROM image_assets
                    WHERE id = %s AND session_id = %s
                    """,
                    (asset_uuid, session_id),
                ).fetchone()
                if row:
                    # Update last_accessed for retention tracking.
                    conn.execute(
                        "UPDATE image_assets SET last_accessed = NOW() WHERE id = %s",
                        (asset_uuid,),
                    )

        if not row:
            return None

        return ImageAsset(
            asset_id=str(row["id"]),
            filename=row["filename"],
            content_type=row["content_type"],
            image_bytes=row["image_bytes"],
            role=row["role"],
        )

    def _insert_asset(
        self,
        asset_id: UUID,
        session_id: str,
        role: str,
        filename: Optional[str],
        content_type: str,
        image_bytes: bytes,
    ) -> None:
        with log_timing("db insert image_assets", logger):
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    """
                    INSERT INTO image_assets
                    (id, session_id, role, filename, content_type, image_bytes)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (asset_id, session_id, role, filename, content_type, image_bytes),
                )


def extension_for_mime(content_type: str) -> str:
    return MIME_EXTENSIONS.get(content_type, ".png")


def _normalize_suffix(suffix: str) -> str:
    if not suffix:
        return ""
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    return suffix.lower()


def _suffix_from_name(name: str) -> str:
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1]


def _resolve_content_type(suffix: str, mimetype: Optional[str]) -> str:
    if mimetype and mimetype.startswith("image/"):
        return mimetype
    return IMAGE_MIME_TYPES.get(suffix, "application/octet-stream")


def _secure_filename(name: str) -> str:
    safe = (name or "").strip()
    if not safe:
        return "upload"
    safe = Path(safe).name
    safe = safe.replace(" ", "_")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", safe)
    safe = safe.strip("._")
    return safe or "upload"


def _coerce_uuid(value: str) -> Optional[UUID]:
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None
