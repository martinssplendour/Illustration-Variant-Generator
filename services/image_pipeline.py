"""Image processing pipeline orchestration and AI handling."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image

from .ai.base import ImageEditor
from .timing import log_timing


@dataclass(frozen=True)
class ProcessingResult:
    result_path: Path
    prompt_used: Optional[str]
    status_message: str
    warning_message: Optional[str]


class AIProcessingError(RuntimeError):
    pass


class ImagePipeline:
    def __init__(
        self,
        result_dir: Path,
        editor: Optional[ImageEditor],
        ai_label: str,
        ai_suffix: str,
    ) -> None:
        self._result_dir = result_dir
        self._editor = editor
        self._ai_label = ai_label
        self._ai_suffix = ai_suffix

    @property
    def ai_available(self) -> bool:
        return bool(self._editor and self._editor.available)

    @property
    def ai_label(self) -> str:
        return self._ai_label

    def process(
        self,
        source_path: Path,
        prompt: str,
        output_stem: str,
        style_rules: str | None = None,
        style_reference_bytes: bytes | None = None,
        result_dir: Path | None = None,
    ) -> ProcessingResult:
        logger = logging.getLogger(__name__)
        with log_timing("pipeline process", logger):
            output_dir = result_dir or self._result_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            prompt_used = prompt or None
            warning_message = None
            final_path: Optional[Path] = None
            status_message = ""

            if prompt:
                if not self.ai_available:
                    # No AI available for a prompt-driven request; fail fast.
                    raise AIProcessingError("AI unavailable; please retry.")
                try:
                    with log_timing(f"ai generate ({self._ai_label})", logger):
                        ai_bytes = (
                            self._editor.edit_image(
                                source_path,
                                prompt,
                                style_rules=style_rules,
                                style_reference_bytes=style_reference_bytes,
                            )
                            if self._editor
                            else None
                        )
                except Exception as exc:
                    raise AIProcessingError(str(exc) or "AI generation failed; please retry.") from exc
                if not ai_bytes:
                    # Treat empty AI responses as a failed generation.
                    raise AIProcessingError("AI generation failed; please retry.")
                final_path = output_dir / f"{output_stem}_{self._ai_suffix}.png"
                final_path.write_bytes(ai_bytes)
                status_message = f"AI variation applied using {self._ai_label}"

            if final_path is None:
                final_path = output_dir / f"{output_stem}_source.png"
                with log_timing("pipeline save source", logger):
                    with Image.open(source_path) as img:
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        img.save(final_path, "PNG")
                if not status_message:
                    status_message = "Source image returned"

            return ProcessingResult(
                result_path=final_path,
                prompt_used=prompt_used,
                status_message=status_message,
                warning_message=warning_message,
            )
