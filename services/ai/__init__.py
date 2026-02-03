"""Factory for building the configured AI image editor."""

from __future__ import annotations

from typing import Optional

from .base import ImageEditor
from .nano_banana import NanoBananaEditor


def build_image_editor(config: dict) -> Optional[ImageEditor]:
    provider = str(config.get("IMAGE_PROVIDER", "")).lower()
    if provider == "nano_banana":
        fast_mode = bool(config.get("FAST_MODE"))
        model_name = config.get("GEMINI_MODEL", "gemini-3-pro-image-preview")
        if fast_mode:
            model_name = config.get("GEMINI_MODEL_FAST", "gemini-2.5-flash-image")
        editor = NanoBananaEditor(
            api_key=config.get("GEMINI_API_KEY", ""),
            model_name=model_name,
            fast_mode=fast_mode,
            reference_max_size=int(config.get("FAST_REFERENCE_MAX_SIZE", 256)),
            timeout_seconds=float(config.get("GEMINI_TIMEOUT_SECONDS", 60)),
            max_retries=int(config.get("GEMINI_MAX_RETRIES", 2)),
            backoff_base_seconds=float(config.get("GEMINI_BACKOFF_BASE_SECONDS", 1)),
            backoff_max_seconds=float(config.get("GEMINI_BACKOFF_MAX_SECONDS", 8)),
            circuit_breaker_threshold=int(config.get("GEMINI_CB_THRESHOLD", 5)),
            circuit_breaker_cooldown_seconds=float(
                config.get("GEMINI_CB_COOLDOWN_SECONDS", 60)
            ),
        )
        return editor if editor.available else None

    return None


__all__ = ["ImageEditor", "build_image_editor", "NanoBananaEditor"]
