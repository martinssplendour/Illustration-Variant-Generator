"""Timing context helper for performance logging."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator, Optional


@contextmanager
def log_timing(label: str, logger: Optional[logging.Logger] = None) -> Iterator[None]:
    """Measures elapsed time for a block and logs it in milliseconds."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        (logger or logging.getLogger(__name__)).info(
            "[Timing] %s: %.1f ms", label, elapsed_ms
        )
