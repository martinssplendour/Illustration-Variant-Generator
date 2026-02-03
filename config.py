"""Environment-driven configuration values for the IVG application."""

from __future__ import annotations

import os


class BaseConfig:
    SECRET_KEY = os.getenv("APP_SECRET_KEY", "dev-secret-2025")
    ENV = os.getenv("APP_ENV", "development").lower()
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH_MB", "10")) * 1024 * 1024
    AUTO_MIGRATE = (
        os.getenv("AUTO_MIGRATE", "false" if ENV == "production" else "true").lower()
        == "true"
    )
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-pro-image-preview")
    GEMINI_MODEL_FAST = os.getenv("GEMINI_MODEL_FAST", "gemini-2.5-flash-image")
    GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "60"))
    GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "2"))
    GEMINI_BACKOFF_BASE_SECONDS = float(os.getenv("GEMINI_BACKOFF_BASE_SECONDS", "1"))
    GEMINI_BACKOFF_MAX_SECONDS = float(os.getenv("GEMINI_BACKOFF_MAX_SECONDS", "8"))
    GEMINI_CB_THRESHOLD = int(os.getenv("GEMINI_CB_THRESHOLD", "5"))
    GEMINI_CB_COOLDOWN_SECONDS = float(os.getenv("GEMINI_CB_COOLDOWN_SECONDS", "60"))
    FAST_MODE = os.getenv("FAST_MODE", "false").lower() == "true"
    FAST_REFERENCE_MAX_SIZE = int(os.getenv("FAST_REFERENCE_MAX_SIZE", "256"))

    ASYNC_TASKS_ENABLED = os.getenv("ASYNC_TASKS_ENABLED", "true").lower() == "true"
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
    CELERY_TASK_ALWAYS_EAGER = (
        os.getenv("CELERY_TASK_ALWAYS_EAGER", "false").lower() == "true"
    )
    CELERY_RESULT_EXPIRES = int(os.getenv("CELERY_RESULT_EXPIRES", "86400"))
    CELERY_QUEUE_GENERATION = os.getenv("CELERY_QUEUE_GENERATION", "ivg_generate")
    CELERY_QUEUE_BG_REMOVE = os.getenv("CELERY_QUEUE_BG_REMOVE", "ivg_bg")
    CELERY_WORKER_AUTOSCALE_MIN = int(os.getenv("CELERY_WORKER_AUTOSCALE_MIN", "0"))
    CELERY_WORKER_AUTOSCALE_MAX = int(os.getenv("CELERY_WORKER_AUTOSCALE_MAX", "0"))

    IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "nano_banana").lower()
    CLEANUP_ON_START = os.getenv("CLEANUP_ON_START", "false").lower() == "true"
    CLEANUP_MAX_AGE_MINUTES = int(os.getenv("CLEANUP_MAX_AGE_MINUTES", "0"))
    STYLE_RULES_MAX_CHARS = int(os.getenv("STYLE_RULES_MAX_CHARS", "4000"))
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    ASSET_TTL_UPLOAD_DAYS = int(os.getenv("ASSET_TTL_UPLOAD_DAYS", "14"))
    ASSET_TTL_RESULT_DAYS = int(os.getenv("ASSET_TTL_RESULT_DAYS", "30"))
    ASSET_TTL_BG_REMOVED_DAYS = int(os.getenv("ASSET_TTL_BG_REMOVED_DAYS", "30"))
    ASSET_GRACE_DAYS = int(os.getenv("ASSET_GRACE_DAYS", "7"))
    HISTORY_TTL_DAYS = int(os.getenv("HISTORY_TTL_DAYS", "90"))

    BACKGROUND_REMOVAL_MODEL = os.getenv("BACKGROUND_REMOVAL_MODEL", "u2net")
    BACKGROUND_REMOVAL_LAZY_INIT = (
        os.getenv("BACKGROUND_REMOVAL_LAZY_INIT", "false").lower() == "true"
    )
    BACKGROUND_REMOVAL_ALPHA_MATTING = (
        os.getenv("BACKGROUND_REMOVAL_ALPHA_MATTING", "true").lower() == "true"
    )
    BACKGROUND_REMOVAL_FG_THRESHOLD = int(
        os.getenv("BACKGROUND_REMOVAL_FG_THRESHOLD", "240")
    )
    BACKGROUND_REMOVAL_BG_THRESHOLD = int(
        os.getenv("BACKGROUND_REMOVAL_BG_THRESHOLD", "10")
    )
    BACKGROUND_REMOVAL_ERODE_SIZE = int(
        os.getenv("BACKGROUND_REMOVAL_ERODE_SIZE", "10")
    )
    BACKGROUND_REMOVAL_POST_PROCESS = (
        os.getenv("BACKGROUND_REMOVAL_POST_PROCESS", "true").lower() == "true"
    )
    BACKGROUND_REMOVAL_MODEL_FAST = os.getenv(
        "BACKGROUND_REMOVAL_MODEL_FAST", "birefnet-general-lite"
    )
    BACKGROUND_REMOVAL_FAST_ALPHA_MATTING = (
        os.getenv("BACKGROUND_REMOVAL_FAST_ALPHA_MATTING", "false").lower() == "true"
    )
    BACKGROUND_REMOVAL_FAST_POST_PROCESS = (
        os.getenv("BACKGROUND_REMOVAL_FAST_POST_PROCESS", "false").lower() == "true"
    )

    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False


class DevelopmentConfig(BaseConfig):
    pass


class ProductionConfig(BaseConfig):
    SESSION_COOKIE_SECURE = True


class TestingConfig(BaseConfig):
    TESTING = True


def get_config_class() -> type[BaseConfig]:
    env = os.getenv("APP_ENV", "development").lower()
    if env == "production":
        return ProductionConfig
    if env == "testing":
        return TestingConfig
    return DevelopmentConfig
