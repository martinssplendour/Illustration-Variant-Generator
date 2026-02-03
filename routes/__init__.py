"""Route registration for API and web endpoints."""

from __future__ import annotations

from fastapi import FastAPI

from .api import api_router
from .web import web_router


def register_routes(app: FastAPI) -> None:
    app.include_router(web_router)
    app.include_router(api_router)
