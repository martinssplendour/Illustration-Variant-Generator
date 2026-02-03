"""Application factory that wires configuration, services, middleware, and routes."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

if os.getenv("LOAD_DOTENV", "1").lower() == "1":
    load_dotenv(os.getenv("DOTENV_FILE") or None)

try:
    from config import BaseConfig, get_config_class
    from logging_config import configure_logging
    from paths import RESULT_DIR, UPLOAD_DIR, ensure_directories
    from routes import register_routes
    from services import AppServices
    from services.ai import build_image_editor
    from services.background_removal import BackgroundRemovalService
    from services.history import GenerationHistoryStore
    from services.image_pipeline import ImagePipeline
    from services.image_assets import ImageAssetStore
    from services.cleanup import cleanup_folder
    from services.styles_postgres import PostgresStyleCatalog
except ImportError:  # pragma: no cover
    from .config import BaseConfig, get_config_class
    from .logging_config import configure_logging
    from .paths import RESULT_DIR, UPLOAD_DIR, ensure_directories
    from .routes import register_routes
    from .services import AppServices
    from .services.ai import build_image_editor
    from .services.background_removal import BackgroundRemovalService
    from .services.history import GenerationHistoryStore
    from .services.image_pipeline import ImagePipeline
    from .services.image_assets import ImageAssetStore
    from .services.cleanup import cleanup_folder
    from .services.styles_postgres import PostgresStyleCatalog


def _resolve_ai_metadata(config: dict) -> tuple[str, str]:
    provider = str(config.get("IMAGE_PROVIDER", "nano_banana")).lower()
    if provider == "nano_banana":
        model_name = str(config.get("GEMINI_MODEL", "gemini-3-pro-image-preview"))
        if str(config.get("FAST_MODE", "false")).lower() == "true":
            model_name = str(config.get("GEMINI_MODEL_FAST", "gemini-2.5-flash-image"))
        label_map = {
            "gemini-3-pro-image-preview": "Gemini 3 Pro Image Preview",
            "gemini-2.5-flash-image": "Gemini 2.5 Flash Image",
        }
        model_label = label_map.get(model_name, model_name)
        return f"Nano Banana ({model_label})", "nano"
    return "AI", "ai"


logger = logging.getLogger(__name__)


class MaxBodySizeExceeded(Exception):
    pass


class MaxBodySizeMiddleware:
    def __init__(self, app: FastAPI, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or self.max_body_size <= 0:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except (TypeError, ValueError):
                declared_length = 0
            if declared_length > self.max_body_size:
                await _send_too_large(send)
                return

        received = 0
        response_started = False

        async def receive_wrapper():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                received += len(body)
                if received > self.max_body_size:
                    raise MaxBodySizeExceeded()
            return message

        async def send_wrapper(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except MaxBodySizeExceeded:
            if response_started:
                return
            await _send_too_large(send)


async def _send_too_large(send) -> None:
    payload = b'{"error":"Request body too large."}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def create_app(config_class: type[BaseConfig] | None = None) -> FastAPI:
    app = FastAPI()
    app_config = config_class or get_config_class()

    configure_logging(getattr(app_config, "LOG_LEVEL", "INFO"))
    ensure_directories()
    max_body_size = int(getattr(app_config, "MAX_CONTENT_LENGTH", 0) or 0)
    if max_body_size > 0:
        # Enforce request size limits early to protect the server.
        app.add_middleware(MaxBodySizeMiddleware, max_body_size=max_body_size)

    db_url = getattr(app_config, "DATABASE_URL", "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL is required for image storage.")
    image_store = ImageAssetStore(db_url, getattr(app_config, "ALLOWED_EXTENSIONS", []))
    history_store = GenerationHistoryStore(db_url)
    auto_migrate = bool(getattr(app_config, "AUTO_MIGRATE", False))
    if auto_migrate:
        try:
            image_store.ensure_schema()
        except Exception as exc:
            logger.error("Failed to initialize image storage: %s", exc)
            raise
        try:
            history_store.ensure_schema()
        except Exception as exc:
            logger.error("Failed to initialize history storage: %s", exc)
            raise

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
    background_model = getattr(app_config, "BACKGROUND_REMOVAL_MODEL", "u2net")
    alpha_matting = getattr(app_config, "BACKGROUND_REMOVAL_ALPHA_MATTING", True)
    post_process = getattr(app_config, "BACKGROUND_REMOVAL_POST_PROCESS", True)
    if fast_mode:
        background_model = getattr(
            app_config, "BACKGROUND_REMOVAL_MODEL_FAST", "birefnet-general-lite"
        )
        alpha_matting = getattr(app_config, "BACKGROUND_REMOVAL_FAST_ALPHA_MATTING", False)
        post_process = getattr(app_config, "BACKGROUND_REMOVAL_FAST_POST_PROCESS", False)
    background_removal = BackgroundRemovalService(
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
    styles = PostgresStyleCatalog(db_url, getattr(app_config, "STYLE_RULES_MAX_CHARS", 4000))
    app.state.services = AppServices(
        assets=image_store,
        pipeline=pipeline,
        background_removal=background_removal,
        styles=styles,
        history=history_store,
    )

    app.state.config = app_config
    app.state.templates = Jinja2Templates(directory="templates")
    app.mount("/static", StaticFiles(directory="static"), name="static")
    app.add_middleware(
        SessionMiddleware,
        secret_key=getattr(app_config, "SECRET_KEY", "dev-secret-2025"),
        same_site=getattr(app_config, "SESSION_COOKIE_SAMESITE", "Lax"),
        https_only=getattr(app_config, "SESSION_COOKIE_SECURE", False),
    )

    register_routes(app)

    if getattr(app_config, "CLEANUP_ON_START", False):
        # Optional housekeeping for temp/runtime directories on startup.
        cleanup_folder(UPLOAD_DIR, getattr(app_config, "CLEANUP_MAX_AGE_MINUTES", 0))
        cleanup_folder(RESULT_DIR, getattr(app_config, "CLEANUP_MAX_AGE_MINUTES", 0))

    if (
        getattr(app_config, "ENV", "development") == "production"
        and getattr(app_config, "SECRET_KEY", "dev-secret-2025") == "dev-secret-2025"
    ):
        logger.warning("Using default SECRET_KEY in production.")

    return app
