"""Background removal service wrapper around rembg."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .timing import log_timing

try:
    from rembg import new_session, remove
except Exception:  # pragma: no cover
    remove = None
    new_session = None

logger = logging.getLogger(__name__)

class BackgroundRemovalService:
    def __init__(
        self,
        result_dir: Path,
        model_name: str = "u2net",
        alpha_matting: bool = True,
        alpha_matting_foreground_threshold: int = 240,
        alpha_matting_background_threshold: int = 10,
        alpha_matting_erode_size: int = 10,
        post_process_mask: bool = True,
        lazy_init: bool = False,
    ) -> None:
        self._result_dir = result_dir
        self._model_name = model_name
        self._lazy_init = lazy_init
        self._alpha_matting = alpha_matting
        self._alpha_matting_foreground_threshold = alpha_matting_foreground_threshold
        self._alpha_matting_background_threshold = alpha_matting_background_threshold
        self._alpha_matting_erode_size = alpha_matting_erode_size
        self._post_process_mask = post_process_mask
        self._session = None
        self.available = remove is not None
        if self.available and new_session is not None and not self._lazy_init:
            try:
                self._session = new_session(model_name)
            except Exception as exc:  # pragma: no cover
                logger.warning("Background removal model init failed: %s", exc)
                self._session = None

    def _ensure_session(self) -> None:
        if self._session is not None or not self.available or new_session is None:
            return
        try:
            # Lazy-init the model session to avoid heavy startup cost.
            self._session = new_session(self._model_name)
        except Exception as exc:  # pragma: no cover
            logger.warning("Background removal model init failed: %s", exc)
            self._session = None

    def remove_background(self, image_path: Path, output_name: str | None = None) -> Optional[Path]:
        if not self.available:
            return None

        try:
            source_bytes = image_path.read_bytes()
            output_bytes = self._remove_bytes(source_bytes)
            if not output_bytes:
                return None
            output_name = output_name or f"{image_path.stem}_nobg.png"
            output_path = self._result_dir / output_name
            output_path.write_bytes(output_bytes)
            return output_path
        except Exception as exc:
            logger.warning("Background removal failed: %s", exc)
            return None

    def remove_background_bytes(self, image_bytes: bytes) -> Optional[bytes]:
        if not self.available:
            return None

        return self._remove_bytes(image_bytes)

    def _remove_bytes(self, image_bytes: bytes) -> Optional[bytes]:
        try:
            self._ensure_session()
            if self._session is None and self._model_name:
                raise RuntimeError("Background removal model unavailable.")
            with log_timing("background removal", logger):
                return remove(
                    image_bytes,
                    session=self._session,
                    alpha_matting=self._alpha_matting,
                    alpha_matting_foreground_threshold=self._alpha_matting_foreground_threshold,
                    alpha_matting_background_threshold=self._alpha_matting_background_threshold,
                    alpha_matting_erode_size=self._alpha_matting_erode_size,
                    post_process_mask=self._post_process_mask,
                )
        except TypeError:
            with log_timing("background removal fallback", logger):
                return remove(image_bytes)
        except Exception as exc:
            logger.warning("Background removal failed: %s", exc)
            return None
