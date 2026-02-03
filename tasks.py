"""Celery tasks that run image variation and background removal jobs."""

from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

if __package__:
    from .celery_app import BG_QUEUE, DEFAULT_QUEUE, celery_app
    from .config import get_config_class
    from .paths import RESULT_DIR, ensure_directories
    from .services import AppServices
    from .services.ai import build_image_editor
    from .services.background_removal import BackgroundRemovalService
    from .services.history import GenerationHistoryStore
    from .services.image_assets import ImageAssetStore, extension_for_mime
    from .services.image_pipeline import ImagePipeline
    from .services.styles_postgres import PostgresStyleCatalog
else:
    from celery_app import BG_QUEUE, DEFAULT_QUEUE, celery_app
    from config import get_config_class
    from paths import RESULT_DIR, ensure_directories
    from services import AppServices
    from services.ai import build_image_editor
    from services.background_removal import BackgroundRemovalService
    from services.history import GenerationHistoryStore
    from services.image_assets import ImageAssetStore, extension_for_mime
    from services.image_pipeline import ImagePipeline
    from services.styles_postgres import PostgresStyleCatalog

logger = logging.getLogger(__name__)

_services: AppServices | None = None
_config_class: type | None = None


def _coerce_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _get_config_class() -> type:
    global _config_class
    if _config_class is None:
        _config_class = get_config_class()
    return _config_class


def _auto_migrate_enabled(app_config: object) -> bool:
    return bool(getattr(app_config, "AUTO_MIGRATE", False))


def _resolve_ai_metadata(config: dict) -> tuple[str, str]:
    provider = str(config.get("IMAGE_PROVIDER", "nano_banana")).lower()
    if provider == "nano_banana":
        model_name = str(config.get("GEMINI_MODEL", "gemini-3-pro-image-preview"))
        if _coerce_bool(config.get("FAST_MODE", False)):
            model_name = str(config.get("GEMINI_MODEL_FAST", "gemini-2.5-flash-image"))
        label_map = {
            "gemini-3-pro-image-preview": "Gemini 3 Pro Image Preview",
            "gemini-2.5-flash-image": "Gemini 2.5 Flash Image",
        }
        model_label = label_map.get(model_name, model_name)
        return f"Nano Banana ({model_label})", "nano"
    return "AI", "ai"


def _build_background_removal(
    app_config: object, fast_mode: bool
) -> BackgroundRemovalService:
    background_model = getattr(app_config, "BACKGROUND_REMOVAL_MODEL", "u2net")
    alpha_matting = getattr(app_config, "BACKGROUND_REMOVAL_ALPHA_MATTING", True)
    post_process = getattr(app_config, "BACKGROUND_REMOVAL_POST_PROCESS", True)
    if fast_mode:
        background_model = getattr(
            app_config, "BACKGROUND_REMOVAL_MODEL_FAST", "birefnet-general-lite"
        )
        alpha_matting = getattr(app_config, "BACKGROUND_REMOVAL_FAST_ALPHA_MATTING", False)
        post_process = getattr(app_config, "BACKGROUND_REMOVAL_FAST_POST_PROCESS", False)
    return BackgroundRemovalService(
        RESULT_DIR,
        model_name=background_model,
        alpha_matting=alpha_matting,
        alpha_matting_foreground_threshold=getattr(
            app_config, "BACKGROUND_REMOVAL_FG_THRESHOLD", 240
        ),
        alpha_matting_background_threshold=getattr(
            app_config, "BACKGROUND_REMOVAL_BG_THRESHOLD", 10
        ),
        alpha_matting_erode_size=getattr(app_config, "BACKGROUND_REMOVAL_ERODE_SIZE", 10),
        post_process_mask=post_process,
        lazy_init=bool(getattr(app_config, "BACKGROUND_REMOVAL_LAZY_INIT", False)),
    )


def _build_services() -> AppServices:
    app_config = _get_config_class()
    ensure_directories()

    db_url = getattr(app_config, "DATABASE_URL", "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required for image storage.")

    image_store = ImageAssetStore(db_url, getattr(app_config, "ALLOWED_EXTENSIONS", []))
    history_store = GenerationHistoryStore(db_url)
    if _auto_migrate_enabled(app_config):
        image_store.ensure_schema()
        history_store.ensure_schema()

    config_values = {key: getattr(app_config, key) for key in dir(app_config) if key.isupper()}
    ai_label, ai_suffix = _resolve_ai_metadata(config_values)
    editor = build_image_editor(config_values)
    pipeline = ImagePipeline(
        result_dir=RESULT_DIR,
        editor=editor,
        ai_label=ai_label,
        ai_suffix=ai_suffix,
    )
    fast_mode = bool(getattr(app_config, "FAST_MODE", False))
    background_removal = _build_background_removal(app_config, fast_mode)
    styles = PostgresStyleCatalog(db_url, getattr(app_config, "STYLE_RULES_MAX_CHARS", 4000))

    return AppServices(
        assets=image_store,
        pipeline=pipeline,
        background_removal=background_removal,
        styles=styles,
        history=history_store,
    )


def _get_services() -> AppServices:
    global _services
    if _services is None:
        _services = _build_services()
    return _services


def _select_pipeline(
    app_config: object, base_pipeline: ImagePipeline, fast_mode: bool
) -> ImagePipeline:
    default_fast = bool(getattr(app_config, "FAST_MODE", False))
    if fast_mode == default_fast:
        return base_pipeline

    config_values = {key: getattr(app_config, key) for key in dir(app_config) if key.isupper()}
    config_values["FAST_MODE"] = fast_mode
    editor = build_image_editor(config_values)
    ai_label, ai_suffix = _resolve_ai_metadata(config_values)
    return ImagePipeline(result_dir=RESULT_DIR, editor=editor, ai_label=ai_label, ai_suffix=ai_suffix)


def _write_temp_image(temp_dir: Path, stem: str, suffix: str, payload: bytes) -> Path:
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    path = temp_dir / f"{stem}{safe_suffix}"
    path.write_bytes(payload)
    return path


@celery_app.task(bind=True, name="ivg.generate_variation", queue=DEFAULT_QUEUE)
def generate_variation_task(
    self,
    session_id: str,
    prompt: str,
    style_id: str = "",
    use_previous: bool = False,
    previous_result: str = "",
    upload_asset_id: str | None = None,
    fast_mode: bool | None = None,
) -> dict:
    if not session_id:
        return {"job_type": "variation", "error": "Missing session id."}

    services = _get_services()
    app_config = _get_config_class()
    pipeline = services.pipeline
    if fast_mode is not None:
        pipeline = _select_pipeline(app_config, pipeline, bool(fast_mode))

    style = services.styles.get_style(style_id) if style_id else None
    if style_id and not style:
        return {"job_type": "variation", "error": "Style not found."}

    uid = uuid4().hex
    source_path: Path | None = None
    original_url: str | None = None

    try:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            # Resolve the source image from upload, previous result, or style reference.
            if upload_asset_id:
                asset = services.assets.get_asset(session_id, upload_asset_id)
                if not asset:
                    return {"job_type": "variation", "error": "Uploaded image not found."}
                suffix = extension_for_mime(asset.content_type)
                source_path = _write_temp_image(temp_path, uid, suffix, asset.image_bytes)
                original_url = f"/api/images/{asset.asset_id}"
            elif use_previous and previous_result:
                asset = services.assets.get_asset(session_id, previous_result)
                if not asset:
                    return {"job_type": "variation", "error": "Previous result not found."}
                suffix = extension_for_mime(asset.content_type)
                source_path = _write_temp_image(temp_path, uid, suffix, asset.image_bytes)
                original_url = f"/api/images/{asset.asset_id}"
            elif style:
                source_path = services.styles.materialize_reference(style, RESULT_DIR)
                if not source_path.is_file():
                    return {"job_type": "variation", "error": "Style reference unavailable."}
                original_url = f"/api/styles/{style.style_id}/reference"
            else:
                return {
                    "job_type": "variation",
                    "error": "Upload an image, enable forward generation, or select a style.",
                }

            style_rules = services.styles.load_rules(style) if style else None
            style_reference_bytes = style.reference_bytes if style else None
            result = pipeline.process(
                source_path,
                (prompt or "").strip(),
                uid,
                style_rules,
                style_reference_bytes=style_reference_bytes,
                result_dir=temp_path,
            )
            result_bytes = result.result_path.read_bytes()
            result_id = services.assets.save_bytes(
                session_id,
                result_bytes,
                "image/png",
                filename=f"{uid}.png",
                role="result",
            )
    except Exception:
        logger.exception("Variation task failed")
        raise

    try:
        services.history.add_entry(session_id, result_id, original_url)
    except Exception as exc:
        logger.warning("Failed to record history: %s", exc)

    return {
        "job_type": "variation",
        "result_id": result_id,
        "original_url": original_url,
        "prompt_used": result.prompt_used,
        "status_message": result.status_message,
        "warning_message": result.warning_message,
    }


@celery_app.task(bind=True, name="ivg.remove_background", queue=BG_QUEUE)
def remove_background_task(
    self,
    session_id: str,
    image_id: str,
    fast_mode: bool = False,
) -> dict:
    if not session_id:
        return {"job_type": "background_removal", "error": "Missing session id."}
    if not image_id:
        return {"job_type": "background_removal", "error": "Missing image id."}

    services = _get_services()
    app_config = _get_config_class()
    removal_service = services.background_removal
    if fast_mode != bool(getattr(app_config, "FAST_MODE", False)):
        # Build a fast-mode background remover when toggled per request.
        removal_service = _build_background_removal(app_config, fast_mode)

    if not removal_service.available:
        return {"job_type": "background_removal", "error": "Background removal unavailable."}

    asset = services.assets.get_asset(session_id, image_id)
    if not asset:
        return {"job_type": "background_removal", "error": "Result image not found."}

    output_bytes = removal_service.remove_background_bytes(asset.image_bytes)
    if not output_bytes:
        return {"job_type": "background_removal", "error": "Background removal failed."}

    output_id = services.assets.save_bytes(
        session_id,
        output_bytes,
        "image/png",
        filename=None,
        role="bg_removed",
    )
    original_url = f"/api/images/{asset.asset_id}"
    try:
        services.history.add_entry(session_id, output_id, original_url)
    except Exception as exc:
        logger.warning("Failed to record background removal history: %s", exc)

    return {
        "job_type": "background_removal",
        "image_id": output_id,
        "original_url": original_url,
    }


__all__ = ["generate_variation_task", "remove_background_task"]
