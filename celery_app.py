"""Celery app configuration and queue routing for background tasks."""

from __future__ import annotations

import os

from celery import Celery
from dotenv import load_dotenv

if os.getenv("LOAD_DOTENV", "1").lower() == "1":
    load_dotenv(os.getenv("DOTENV_FILE") or None)


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


def _default_worker_pool() -> str:
    return "solo" if os.name == "nt" else "prefork"


DEFAULT_QUEUE = os.getenv("CELERY_QUEUE_GENERATION", "ivg_generate")
BG_QUEUE = os.getenv("CELERY_QUEUE_BG_REMOVE", "ivg_bg")


def create_celery_app() -> Celery:
    async_enabled = _bool_env("ASYNC_TASKS_ENABLED", "true")
    if async_enabled:
        broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
        result_backend = os.getenv("CELERY_RESULT_BACKEND", broker_url)
    else:
        # In local/functional mode, run tasks eagerly with in-memory broker/backend.
        broker_url = "memory://"
        result_backend = "cache+memory://"
    app = Celery("ivg", broker=broker_url, backend=result_backend, include=["tasks"])
    autoscale_min = _int_env("CELERY_WORKER_AUTOSCALE_MIN", "0")
    autoscale_max = _int_env("CELERY_WORKER_AUTOSCALE_MAX", "0")
    worker_pool = os.getenv("CELERY_WORKER_POOL", _default_worker_pool())
    app.conf.update(
        task_track_started=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        broker_connection_retry_on_startup=True,
        task_always_eager=_bool_env("CELERY_TASK_ALWAYS_EAGER", "false") or not async_enabled,
        task_eager_propagates=True,
        task_default_queue=DEFAULT_QUEUE,
        task_routes={
            "ivg.generate_variation": {"queue": DEFAULT_QUEUE},
            "ivg.remove_background": {"queue": BG_QUEUE},
        },
        result_expires=_int_env("CELERY_RESULT_EXPIRES", "86400"),
        worker_pool=worker_pool,
    )
    if autoscale_min > 0 and autoscale_max >= autoscale_min:
        app.conf.worker_autoscale = (autoscale_max, autoscale_min)
    return app


celery_app = create_celery_app()

__all__ = ["BG_QUEUE", "DEFAULT_QUEUE", "celery_app", "create_celery_app"]
