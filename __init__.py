"""Package initializer for the image variation FastAPI app."""
try:
    from app_factory import create_app
except ImportError:  # pragma: no cover
    from .app_factory import create_app

__all__ = ["create_app"]
