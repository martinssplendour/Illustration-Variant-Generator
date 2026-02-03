"""JSON API endpoints for styles, assets, jobs, and generation."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional
from urllib.parse import urljoin

from celery.result import AsyncResult
from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

try:
    from paths import RESULT_DIR
    from routes.utils import get_fast_mode, get_session_id, set_fast_mode
    from celery_app import celery_app
    from services import AppServices
    from services.background_removal import BackgroundRemovalService
    from services.image_assets import StorageError, extension_for_mime
    from services.image_pipeline import AIProcessingError
    from tasks import generate_variation_task, remove_background_task
except ImportError:  # pragma: no cover
    from ..paths import RESULT_DIR
    from .utils import get_fast_mode, get_session_id, set_fast_mode
    from ..celery_app import celery_app
    from ..services import AppServices
    from ..services.background_removal import BackgroundRemovalService
    from ..services.image_assets import StorageError, extension_for_mime
    from ..services.image_pipeline import AIProcessingError
    from ..tasks import generate_variation_task, remove_background_task

api_router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


def _absolute_url(request: Request, url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(str(request.base_url), url.lstrip("/"))


@api_router.get("/health")
def health_check() -> dict:
    return {"status": "ok"}

def _map_task_status(state: str) -> str:
    normalized = (state or "").upper()
    if normalized in {"PENDING", "RECEIVED"}:
        return "pending"
    if normalized in {"STARTED", "RETRY"}:
        return "processing"
    if normalized == "SUCCESS":
        return "complete"
    if normalized == "FAILURE":
        return "failed"
    return "pending"


def _format_variation_result(request: Request, payload: dict) -> dict:
    result_id = payload.get("result_id")
    result_url = (
        str(request.url_for("api_image_asset", image_id=result_id)) if result_id else None
    )
    original_url = payload.get("original_url")
    if original_url:
        original_url = _absolute_url(request, original_url)
    return {
        "result_url": result_url,
        "result_id": result_id,
        "original_url": original_url,
        "prompt_used": payload.get("prompt_used"),
        "status_message": payload.get("status_message"),
        "warning_message": payload.get("warning_message"),
    }


def _format_background_result(request: Request, payload: dict) -> dict:
    image_id = payload.get("image_id")
    result_url = (
        str(request.url_for("api_image_asset", image_id=image_id)) if image_id else None
    )
    original_url = payload.get("original_url")
    if original_url:
        original_url = _absolute_url(request, original_url)
    return {
        "result_url": result_url,
        "image_id": image_id,
        "original_url": original_url,
    }


def _build_background_removal(app_config: object, fast_mode: bool) -> BackgroundRemovalService:
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


@api_router.get("/jobs/{job_id}", name="api_job_status")
def job_status(request: Request, job_id: str):
    return _build_job_payload(request, job_id)


def _build_job_payload(request: Request, job_id: str) -> dict:
    result = AsyncResult(job_id, app=celery_app)
    status = _map_task_status(result.state)
    payload: dict[str, object] = {"job_id": job_id, "status": status}

    if status == "failed":
        payload["error"] = str(result.info)
        return payload

    if status != "complete":
        return payload

    result_payload = result.result or {}
    if isinstance(result_payload, dict) and result_payload.get("error"):
        payload["status"] = "failed"
        payload["error"] = result_payload.get("error")
        return payload

    if isinstance(result_payload, dict):
        job_type = result_payload.get("job_type")
        payload["job_type"] = job_type
        if job_type == "variation":
            payload["result"] = _format_variation_result(request, result_payload)
        elif job_type == "background_removal":
            payload["result"] = _format_background_result(request, result_payload)
        else:
            payload["result"] = result_payload
    else:
        payload["result"] = result_payload

    return payload


@api_router.get("/jobs/{job_id}/stream", name="api_job_stream")
async def job_stream(
    request: Request,
    job_id: str,
    poll_interval: float = Query(default=1.5, ge=0.2, le=10),
):
    async def event_generator():
        last_payload = None
        while True:
            if await request.is_disconnected():
                break
            payload = _build_job_payload(request, job_id)
            data = json.dumps(payload, ensure_ascii=True)
            if data != last_payload:
                # Only push updates when the payload changes.
                yield f"data: {data}\n\n"
                last_payload = data
            if payload.get("status") in {"complete", "failed"}:
                break
            await asyncio.sleep(poll_interval)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )


@api_router.get("/styles")
def list_styles(request: Request):
    services: AppServices = request.app.state.services
    styles = [{"id": style.style_id, "name": style.name} for style in services.styles.list_styles()]
    return {"styles": styles}


@api_router.get("/styles/{style_id}/reference", name="api_style_reference")
def style_reference(request: Request, style_id: str):
    services: AppServices = request.app.state.services
    style = services.styles.get_style(style_id)
    if not style:
        return JSONResponse({"error": "Style not found."}, status_code=404)
    reference_path = services.styles.materialize_reference(style, RESULT_DIR)
    if not reference_path.is_file():
        return JSONResponse({"error": "Style reference unavailable."}, status_code=404)
    return FileResponse(reference_path)


@api_router.get("/history")
def list_history(request: Request, limit: Optional[int] = Query(default=None, ge=1)):
    services: AppServices = request.app.state.services
    session_id = get_session_id(request)
    entries = services.history.list_entries(session_id, limit=limit)
    history = []
    for entry in entries:
        result_url = request.url_for("api_image_asset", image_id=entry.result_id)
        original_url = _absolute_url(request, entry.original_url) if entry.original_url else result_url
        history.append(
            {
                "result_id": entry.result_id,
                "result_url": str(result_url),
                "original_url": str(original_url),
                "created_at": entry.created_at.isoformat(),
            }
        )
    return {"history": history}


@api_router.get("/images/{image_id}", name="api_image_asset")
def image_asset(request: Request, image_id: str):
    session_id = get_session_id(request)
    services: AppServices = request.app.state.services
    asset = services.assets.get_asset(session_id, image_id)
    if not asset:
        return JSONResponse({"error": "Image not found."}, status_code=404)
    download_name = asset.filename or f"image{extension_for_mime(asset.content_type)}"
    headers = {"Content-Disposition": f'inline; filename="{download_name}"'}
    return Response(content=asset.image_bytes, media_type=asset.content_type, headers=headers)


@api_router.post("/variations")
async def create_variation(
    request: Request,
    image: Optional[UploadFile] = File(default=None),
    prompt: str = Form(default=""),
    style_id: str = Form(default=""),
    use_previous: Optional[str] = Form(default=None),
    previous_result: str = Form(default=""),
    fast_mode: list[str] = Form(default=[]),
):
    services: AppServices = request.app.state.services
    session_id = get_session_id(request)
    use_previous_flag = (use_previous or "").lower() in {"true", "1", "on", "yes"}
    app_config = request.app.state.config
    fast_mode_flag = (
        any((value or "").lower() in {"true", "1", "on", "yes"} for value in fast_mode)
        if fast_mode
        else get_fast_mode(request, bool(getattr(app_config, "FAST_MODE", False)))
    )
    if fast_mode:
        set_fast_mode(request, fast_mode_flag)

    upload_asset_id = None
    if image and image.filename:
        try:
            payload = await image.read()
            stored = await run_in_threadpool(
                services.assets.save_upload_bytes,
                filename=image.filename,
                content_type=image.content_type,
                image_bytes=payload,
                session_id=session_id,
            )
            upload_asset_id = stored.asset_id
        except StorageError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    if not upload_asset_id and not (use_previous_flag and previous_result) and not style_id:
        return JSONResponse(
            {"error": "Upload an image, enable forward generation, or select a style."},
            status_code=400,
        )

    try:
        task = generate_variation_task.delay(
            session_id=session_id,
            prompt=prompt.strip(),
            style_id=style_id,
            use_previous=use_previous_flag,
            previous_result=previous_result,
            upload_asset_id=upload_asset_id,
            fast_mode=fast_mode_flag,
        )
    except AIProcessingError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except Exception:
        logger.exception("Failed to enqueue variation task")
        return JSONResponse({"error": "Failed to start generation."}, status_code=503)
    return JSONResponse(
        {
            "job_id": task.id,
            "status_url": str(request.url_for("api_job_status", job_id=task.id)),
            "stream_url": str(request.url_for("api_job_stream", job_id=task.id)),
            "status": "pending",
        },
        status_code=202,
    )


@api_router.post("/remove-background")
async def remove_background(request: Request):
    app_config = request.app.state.config
    fast_mode_flag = get_fast_mode(request, bool(getattr(app_config, "FAST_MODE", False)))
    session_id = get_session_id(request)
    jobs_enabled = bool(getattr(app_config, "ASYNC_TASKS_ENABLED", True))
    resolved_id = ""
    try:
        form = await request.form()
        resolved_id = str(form.get("image_id") or "").strip()
    except Exception:
        pass
    if not resolved_id:
        try:
            payload = await request.json()
            if isinstance(payload, dict):
                resolved_id = str(payload.get("image_id") or "").strip()
        except Exception:
            pass
    if not resolved_id:
        resolved_id = (request.query_params.get("image_id") or "").strip()
    if not resolved_id:
        return JSONResponse({"error": "Missing image id."}, status_code=400)

    if jobs_enabled:
        task = remove_background_task.delay(
            session_id=session_id,
            image_id=resolved_id,
            fast_mode=fast_mode_flag,
        )
        return JSONResponse(
            {
                "job_id": task.id,
                "status_url": str(request.url_for("api_job_status", job_id=task.id)),
                "stream_url": str(request.url_for("api_job_stream", job_id=task.id)),
                "status": "pending",
            },
            status_code=202,
        )

    services: AppServices = request.app.state.services
    removal_service = services.background_removal
    if fast_mode_flag != bool(getattr(app_config, "FAST_MODE", False)):
        removal_service = _build_background_removal(app_config, fast_mode_flag)
    if not removal_service.available:
        return JSONResponse({"error": "Background removal unavailable."}, status_code=503)

    asset = await run_in_threadpool(services.assets.get_asset, session_id, resolved_id)
    if not asset:
        return JSONResponse({"error": "Result image not found."}, status_code=404)

    output_bytes = await run_in_threadpool(
        removal_service.remove_background_bytes, asset.image_bytes
    )
    if not output_bytes:
        return JSONResponse({"error": "Background removal failed."}, status_code=500)

    output_id = await run_in_threadpool(
        services.assets.save_bytes,
        session_id,
        output_bytes,
        "image/png",
        None,
        "bg_removed",
    )
    original_url = f"/api/images/{asset.asset_id}"
    try:
        await run_in_threadpool(services.history.add_entry, session_id, output_id, original_url)
    except Exception:
        pass

    result_payload = {
        "job_type": "background_removal",
        "image_id": output_id,
        "original_url": original_url,
    }
    return JSONResponse(
        {"status": "complete", "result": _format_background_result(request, result_payload)}
    )
