"""Server-rendered UI routes for the IVG web interface."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional
from uuid import uuid4

import logging

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

try:
    from paths import RESULT_DIR
    from routes.utils import (
        add_flash,
        get_fast_mode,
        get_session_id,
        pop_flashes,
        set_fast_mode,
        write_temp_image,
    )
    from services import AppServices
    from services.ai import build_image_editor
    from services.image_assets import StorageError, extension_for_mime
    from services.image_pipeline import AIProcessingError, ImagePipeline
except ImportError:  # pragma: no cover
    from ..paths import RESULT_DIR
    from .utils import (
        add_flash,
        get_fast_mode,
        get_session_id,
        pop_flashes,
        set_fast_mode,
        write_temp_image,
    )
    from ..services import AppServices
    from ..services.ai import build_image_editor
    from ..services.image_assets import StorageError, extension_for_mime
    from ..services.image_pipeline import AIProcessingError, ImagePipeline

web_router = APIRouter()
logger = logging.getLogger(__name__)


def _resolve_ai_metadata(config: dict, fast_mode: bool) -> tuple[str, str]:
    provider = str(config.get("IMAGE_PROVIDER", "nano_banana")).lower()
    if provider == "nano_banana":
        model_name = config.get("GEMINI_MODEL", "gemini-3-pro-image-preview")
        if fast_mode:
            model_name = config.get("GEMINI_MODEL_FAST", "gemini-2.5-flash-image")
        label_map = {
            "gemini-3-pro-image-preview": "Gemini 3 Pro Image Preview",
            "gemini-2.5-flash-image": "Gemini 2.5 Flash Image",
        }
        model_label = label_map.get(model_name, model_name)
        return f"Nano Banana ({model_label})", "nano"
    return "AI", "ai"


def _select_pipeline(
    app_config: object, base_pipeline: ImagePipeline, fast_mode: bool
) -> ImagePipeline:
    default_fast = bool(getattr(app_config, "FAST_MODE", False))
    if fast_mode == default_fast:
        return base_pipeline

    config_values = {key: getattr(app_config, key) for key in dir(app_config) if key.isupper()}
    config_values["FAST_MODE"] = fast_mode
    editor = build_image_editor(config_values)
    ai_label, ai_suffix = _resolve_ai_metadata(config_values, fast_mode)
    return ImagePipeline(result_dir=RESULT_DIR, editor=editor, ai_label=ai_label, ai_suffix=ai_suffix)


def _get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


@web_router.get("/", name="web_index")
def index(request: Request):
    services: AppServices = request.app.state.services
    app_config = request.app.state.config
    config_values = {key: getattr(app_config, key) for key in dir(app_config) if key.isupper()}
    fast_mode_checked = get_fast_mode(request, bool(getattr(app_config, "FAST_MODE", False)))
    ai_label, _ = _resolve_ai_metadata(config_values, fast_mode_checked)
    jobs_enabled = bool(getattr(app_config, "ASYNC_TASKS_ENABLED", True))
    context = {
        "request": request,
        "nano_available": services.pipeline.ai_available,
        "ai_label": ai_label,
        "result_url": None,
        "result_id": None,
        "original_url": None,
        "prompt_used": None,
        "prompt_value": "",
        "status_message": None,
        "selected_style_id": "",
        "use_previous_checked": False,
        "fast_mode_checked": fast_mode_checked,
        "source_image_id": "",
        "messages": pop_flashes(request),
        "jobs_enabled": jobs_enabled,
    }
    return _get_templates(request).TemplateResponse("index.html", context)


@web_router.post("/")
async def create_variation(
    request: Request,
    image: Optional[UploadFile] = File(default=None),
    prompt: str = Form(default=""),
    style_id: str = Form(default=""),
    use_previous: Optional[str] = Form(default=None),
    previous_result: str = Form(default=""),
    regenerate: Optional[str] = Form(default=None),
    source_image_id: str = Form(default=""),
    fast_mode: list[str] = Form(default=[]),
):
    services: AppServices = request.app.state.services
    session_id = get_session_id(request)
    use_previous_flag = (use_previous or "").lower() == "on"
    regenerate_flag = (regenerate or "").lower() in {"true", "1", "on", "yes"}
    source_image_id = (source_image_id or "").strip()
    fast_mode_flag = any(
        (value or "").lower() in {"true", "1", "on", "yes"} for value in fast_mode
    )
    set_fast_mode(request, fast_mode_flag)
    app_config = request.app.state.config
    pipeline = _select_pipeline(app_config, services.pipeline, fast_mode_flag)

    uid = uuid4().hex
    style = services.styles.get_style(style_id) if style_id else None
    if style_id and not style:
        add_flash(request, "Selected style not found.")
        return RedirectResponse(url=str(request.url_for("web_index")), status_code=303)

    source_path = None
    original_url = None
    source_asset_id = ""

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        if image and image.filename:
            try:
                payload = await image.read()
                stored = services.assets.save_upload_bytes(
                    filename=image.filename,
                    content_type=image.content_type,
                    image_bytes=payload,
                    session_id=session_id,
                )
            except StorageError as exc:
                add_flash(request, str(exc))
                return RedirectResponse(url=str(request.url_for("web_index")), status_code=303)
            source_path = write_temp_image(temp_path, uid, stored.suffix, stored.image_bytes)
            original_url = request.url_for("api_image_asset", image_id=stored.asset_id)
            source_asset_id = stored.asset_id
        elif use_previous_flag and previous_result:
            asset = services.assets.get_asset(session_id, previous_result)
            if not asset:
                add_flash(request, "Previous result not found.")
                return RedirectResponse(url=str(request.url_for("web_index")), status_code=303)
            suffix = extension_for_mime(asset.content_type)
            source_path = write_temp_image(temp_path, uid, suffix, asset.image_bytes)
            original_url = request.url_for("api_image_asset", image_id=asset.asset_id)
            source_asset_id = asset.asset_id
        elif regenerate_flag and source_image_id:
            asset = services.assets.get_asset(session_id, source_image_id)
            if not asset:
                add_flash(request, "Source image not found for regenerate.")
                return RedirectResponse(url=str(request.url_for("web_index")), status_code=303)
            suffix = extension_for_mime(asset.content_type)
            source_path = write_temp_image(temp_path, uid, suffix, asset.image_bytes)
            original_url = request.url_for("api_image_asset", image_id=asset.asset_id)
            source_asset_id = asset.asset_id
        elif style:
            source_path = services.styles.materialize_reference(style, RESULT_DIR)
            if not source_path.is_file():
                add_flash(request, "Style reference unavailable.")
                return RedirectResponse(url=str(request.url_for("web_index")), status_code=303)
            original_url = request.url_for("api_style_reference", style_id=style.style_id)
        else:
            add_flash(request, "Upload an image, enable forward generation, or select a style.")
            return RedirectResponse(url=str(request.url_for("web_index")), status_code=303)

        style_rules = services.styles.load_rules(style) if style else None
        style_reference_bytes = style.reference_bytes if style else None
        try:
            result = pipeline.process(
                source_path,
                prompt.strip(),
                uid,
                style_rules,
                style_reference_bytes=style_reference_bytes,
                result_dir=temp_path,
            )
        except AIProcessingError as exc:
            add_flash(request, str(exc))
            return RedirectResponse(url=str(request.url_for("web_index")), status_code=303)
        result_bytes = result.result_path.read_bytes()
        result_id = services.assets.save_bytes(
            session_id,
            result_bytes,
            "image/png",
            filename=f"{uid}.png",
            role="result",
        )
    try:
        services.history.add_entry(session_id, result_id, str(original_url) if original_url else None)
    except Exception as exc:
        logger.warning("Failed to record history: %s", exc)

    if result.warning_message:
        add_flash(request, result.warning_message)

    jobs_enabled = bool(getattr(app_config, "ASYNC_TASKS_ENABLED", True))
    context = {
        "request": request,
        "nano_available": pipeline.ai_available,
        "ai_label": pipeline.ai_label,
        "result_url": str(request.url_for("api_image_asset", image_id=result_id)),
        "result_id": result_id,
        "original_url": str(original_url) if original_url else None,
        "prompt_used": result.prompt_used,
        "status_message": result.status_message,
        "selected_style_id": style_id,
        "use_previous_checked": use_previous_flag,
        "fast_mode_checked": fast_mode_flag,
        "source_image_id": source_asset_id,
        "prompt_value": result.prompt_used or "",
        "messages": pop_flashes(request),
        "jobs_enabled": jobs_enabled,
    }
    return _get_templates(request).TemplateResponse("index.html", context)
