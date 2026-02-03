"""Service container definitions for core app services."""

from __future__ import annotations

from dataclasses import dataclass

from .background_removal import BackgroundRemovalService
from .history import GenerationHistoryStore
from .image_assets import ImageAssetStore
from .image_pipeline import ImagePipeline
from .styles_postgres import PostgresStyleCatalog


@dataclass(frozen=True)
class AppServices:
    assets: ImageAssetStore
    pipeline: ImagePipeline
    background_removal: BackgroundRemovalService
    styles: PostgresStyleCatalog
    history: GenerationHistoryStore


__all__ = [
    "AppServices",
    "BackgroundRemovalService",
    "GenerationHistoryStore",
    "ImageAssetStore",
    "ImagePipeline",
    "PostgresStyleCatalog",
]
