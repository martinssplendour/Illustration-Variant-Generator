"""Protocol definition for AI image editor providers."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol


class ImageEditor(Protocol):
    @property
    def available(self) -> bool:
        ...

    def edit_image(
        self,
        image_path: Path,
        prompt: str,
        style_rules: str | None = None,
        style_reference_bytes: bytes | None = None,
    ) -> Optional[bytes]:
        ...
