"""Shared helpers for session handling and route utilities."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from starlette.requests import Request

SESSION_ID_KEY = "session_id"
FLASH_KEY = "flash_messages"
FAST_MODE_KEY = "fast_mode"


def get_session_id(request: Request) -> str:
    # Create a stable session id for history and asset scoping.
    session_id = request.session.get(SESSION_ID_KEY)
    if not session_id:
        session_id = uuid4().hex
        request.session[SESSION_ID_KEY] = session_id
    return session_id


def add_flash(request: Request, message: str) -> None:
    messages = list(request.session.get(FLASH_KEY, []))
    messages.append(message)
    request.session[FLASH_KEY] = messages


def pop_flashes(request: Request) -> list[str]:
    messages = request.session.pop(FLASH_KEY, [])
    return list(messages)


def set_fast_mode(request: Request, enabled: bool) -> None:
    request.session[FAST_MODE_KEY] = bool(enabled)


def get_fast_mode(request: Request, default: bool = False) -> bool:
    return bool(request.session.get(FAST_MODE_KEY, default))


def write_temp_image(temp_dir: Path, stem: str, suffix: str, payload: bytes) -> Path:
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    path = temp_dir / f"{stem}{safe_suffix}"
    path.write_bytes(payload)
    return path
