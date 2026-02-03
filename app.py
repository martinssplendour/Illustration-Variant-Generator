"""Local entry point for running the FastAPI application with Uvicorn."""

from __future__ import annotations

import os

import uvicorn

from app_factory import create_app

app = create_app()


if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "5001"))
    reload = os.getenv("APP_ENV", "development").lower() == "development"
    uvicorn.run("app:app", host=host, port=port, reload=reload)
