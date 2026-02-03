"""Gemini-based image editor with retries and circuit breaker."""

from __future__ import annotations

import base64
import io
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from typing import Optional

from PIL import Image, ImageFilter

from ..timing import log_timing

genai_client = None
genai_types = None
GENAI_BACKEND = None
try:
    from google import genai as genai_client
    from google.genai import types as genai_types

    GENAI_BACKEND = "genai"
except Exception:
    try:
        import google.generativeai as genai_client

        GENAI_BACKEND = "generativeai"
    except Exception:
        genai_client = None

logger = logging.getLogger(__name__)

# Prompt template enforces style, layout, and content constraints for the model.
SYSTEM_PROMPT_TEMPLATE = """You are the Illustration Variant Generator (IVG), an image-to-image variation engine. Your task is to generate a new illustration that preserves the exact visual style of the provided reference image(s) and the style rules, while applying the user's requested change(s). The style rules come from a PDF guide and are authoritative for *style construction* (brush, linework, palette, shading, CMYK feel). The output must look like it belongs to the same illustration set: line weight, color treatment, shading, proportions, and overall rendering must match precisely.

PRIORITY ORDER (highest to lowest):
1) SOURCE IMAGE: Ground-truth for composition, subject identity, proportions, pose, and overall layout.
2) USER REQUEST: Allowed content changes only where explicitly asked.
3) STYLE GUIDE + REFERENCE IMAGE: Style-only overlay (brush behavior, stroke texture, ink density, CMYK/print feel, palette discipline).

STYLE TRANSFER DIRECTIVE:
- The source image is the only source of content and composition. Treat it as ground-truth.
- The reference image is for style transfer only. Never copy subjects, poses, objects, backgrounds, or layouts from it.
- If the reference image suggests content, ignore it completely and keep the source image content intact.
- Preserve source scale: characters and key objects must remain the same size and framing as the source image.
- Output dimensions must match the source image dimensions exactly. Do not crop, zoom, or reframe.
- Repeat: the output must keep the exact canvas size and framing of the source image.

CONFLICT RULES:
- If the style guide conflicts with the source image's content or structure, preserve the source image and apply only stylistic cues.
- If the user request conflicts with the source image or style rules, preserve the source image and style first.

STYLE IMMUTABLES (must match exactly):
- Linework: match stroke weight, smoothness, and outline treatment.
- Color palette: match saturation, brightness, and temperature; avoid new hues not present.
- Shading: match the level (e.g., flat/low/medium), edge softness, and placement logic.
- Lighting: match direction, intensity, and highlight style.
- Form language: match simplification level (cartoon vs semi-realistic), shape roundness, and proportions.
- Texture/detail: match surface detail level; do not introduce new textures or effects.
- Production feel: match brush type, stroke tapering, fill texture, and CMYK/print-like rendering cues.

CONTENT MUTABLES (can change only if asked):
- Subject identity, pose, clothing, props, facial expression, background elements.

OUTPUT REQUIREMENTS:
- Keep the style identical to the reference; only change content required by the request.
- Maintain clean silhouettes, consistent outlines, and the same color/shape logic.
- Preserve the source image composition and subject identity unless the user explicitly asks to change them.
- Do not add background clutter or decorative elements unless requested.
- If the request conflicts with style rules, preserve style above all else.

PROCESS:
1) Parse style rules and reference image(s).
2) Extract immutable style traits (linework, palette, shading, proportions).
3) Apply the user's requested content change while preserving all immutable traits.
4) Verify the result still looks like the same artist/style.

STYLE RULES:
{style_rules}

SOURCE LAYOUT HINTS:
{layout_hint}

USER REQUEST:
{user_prompt}
"""


class NanoBananaError(RuntimeError):
    pass


class NanoBananaRetryableError(NanoBananaError):
    pass


class _CircuitBreaker:
    """Simple circuit breaker to stop hammering the provider during outages."""
    def __init__(self, threshold: int, cooldown_seconds: float) -> None:
        self._threshold = max(0, int(threshold))
        self._cooldown = max(0.0, float(cooldown_seconds))
        self._failures = 0
        self._opened_at = 0.0

    def allow(self) -> bool:
        if not self._threshold or not self._cooldown:
            return True
        if self._opened_at == 0.0:
            return True
        if time.monotonic() - self._opened_at >= self._cooldown:
            self._opened_at = 0.0
            self._failures = 0
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = 0.0

    def record_failure(self) -> None:
        if not self._threshold:
            return
        self._failures += 1
        if self._failures >= self._threshold and self._cooldown:
            self._opened_at = time.monotonic()


class NanoBananaEditor:
    def __init__(
        self,
        api_key: str,
        model_name: str,
        enabled: bool = True,
        fast_mode: bool = False,
        reference_max_size: int = 256,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 8.0,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown_seconds: float = 60.0,
    ) -> None:
        self._client = None
        self._model = None
        self._model_name = model_name
        self._backend = GENAI_BACKEND
        self._fast_mode = fast_mode
        self._reference_max_size = max(0, int(reference_max_size))
        self._timeout_seconds = max(0.0, float(timeout_seconds))
        self._max_retries = max(0, int(max_retries))
        self._backoff_base = max(0.0, float(backoff_base_seconds))
        self._backoff_max = max(0.0, float(backoff_max_seconds))
        self._breaker = _CircuitBreaker(circuit_breaker_threshold, circuit_breaker_cooldown_seconds)
        self.available = bool(enabled) and bool(api_key) and genai_client is not None
        if not self.available:
            logger.info("[NanoBanana] SDK missing or API key not set")
            return
        if self._backend == "genai" and genai_types is None:
            logger.warning("[NanoBanana] genai types unavailable")
            self.available = False
            return

        try:
            if self._backend == "genai":
                self._client = genai_client.Client(api_key=api_key)
            else:
                genai_client.configure(api_key=api_key)
                self._model = genai_client.GenerativeModel(model_name)
            logger.info("[NanoBanana] configured model %s", model_name)
        except Exception as exc:
            logger.warning("[NanoBanana] config error: %s", exc)
            self.available = False

    def edit_image(
        self,
        image_path: Path,
        prompt: str,
        style_rules: str | None = None,
        style_reference_bytes: bytes | None = None,
    ) -> Optional[bytes]:
        if not self.available:
            raise NanoBananaRetryableError("AI unavailable; please retry.")
        if self._backend == "genai":
            if not self._client:
                raise NanoBananaRetryableError("AI client not ready; please retry.")
        else:
            if not self._model:
                raise NanoBananaRetryableError("AI model not ready; please retry.")

        try:
            pil_img = Image.open(image_path).convert("RGB")
            layout_hint = _describe_layout(pil_img)
            combined_prompt = SYSTEM_PROMPT_TEMPLATE.format(
                style_rules=style_rules.strip() if style_rules else "None provided.",
                layout_hint=layout_hint or "None provided.",
                user_prompt=prompt.strip(),
            )
            if self._backend == "genai":
                contents = [genai_types.Part.from_text(text=combined_prompt)]
                if style_reference_bytes:
                    try:
                        style_img = Image.open(io.BytesIO(style_reference_bytes)).convert("RGB")
                        if self._fast_mode:
                            style_img = _downscale_image(style_img, self._reference_max_size)
                        # Style reference is provided as style-only guidance, not content.
                        contents.append(
                            genai_types.Part.from_text(
                                text="STYLE REFERENCE IMAGE (style only; do not copy content or composition)"
                            )
                        )
                        contents.append(
                            genai_types.Part.from_bytes(
                                data=_image_to_png_bytes(style_img), mime_type="image/png"
                            )
                        )
                    except Exception as exc:
                        logger.warning("[NanoBanana] style reference decode failed: %s", exc)
                contents.append(
                    genai_types.Part.from_text(
                        text="SOURCE IMAGE (ground-truth content/composition; preserve unless user requests changes)"
                    )
                )
                contents.append(
                    genai_types.Part.from_bytes(
                        data=_image_to_png_bytes(pil_img), mime_type="image/png"
                    )
                )
                if not self._fast_mode:
                    # Repeat the source image to reinforce layout preservation.
                    contents.append(
                        genai_types.Part.from_text(
                            text="SOURCE IMAGE (repeat for emphasis; do not change layout, scale, or framing)"
                        )
                    )
                    contents.append(
                        genai_types.Part.from_bytes(
                            data=_image_to_png_bytes(pil_img), mime_type="image/png"
                        )
                    )
                def _call_genai():
                    return self._client.models.generate_content(
                        model=self._model_name, contents=contents
                    )

                with log_timing(f"nanobanana generate_content {self._model_name}", logger):
                    response = self._generate_with_retries(_call_genai)
            else:
                contents = [combined_prompt]
                if style_reference_bytes:
                    try:
                        style_img = Image.open(io.BytesIO(style_reference_bytes)).convert("RGB")
                        if self._fast_mode:
                            style_img = _downscale_image(style_img, self._reference_max_size)
                        contents.append(
                            "STYLE REFERENCE IMAGE (style only; do not copy content or composition)"
                        )
                        contents.append(style_img)
                    except Exception as exc:
                        logger.warning("[NanoBanana] style reference decode failed: %s", exc)
                contents.append(
                    "SOURCE IMAGE (ground-truth content/composition; preserve unless user requests changes)"
                )
                contents.append(pil_img)
                if not self._fast_mode:
                    contents.append(
                        "SOURCE IMAGE (repeat for emphasis; do not change layout, scale, or framing)"
                    )
                    contents.append(pil_img)
                def _call_generativeai():
                    return self._model.generate_content(contents, stream=False)

                with log_timing(f"nanobanana generate_content {self._model_name}", logger):
                    response = self._generate_with_retries(_call_generativeai)

            for candidate in response.candidates or []:
                for part in candidate.content.parts or []:
                    inline = getattr(part, "inline_data", None)
                    image_bytes = _inline_image_to_png(inline)
                    if image_bytes:
                        logger.info("[NanoBanana] image generated")
                        return image_bytes

            try:
                text = getattr(response, "text", "")
            except Exception as exc:
                logger.info("[NanoBanana] non-text response: %s", exc)
            else:
                if text:
                    logger.info("[NanoBanana] text response: %s", text[:200])
            raise NanoBananaRetryableError("AI generation failed; please retry.")

        except NanoBananaError:
            raise
        except Exception as exc:
            logger.warning("[NanoBanana] edit error: %s", exc)
            raise NanoBananaRetryableError("AI generation failed; please retry.") from exc

    def _generate_with_retries(self, call_fn):
        # Retry transient provider failures with backoff and circuit breaker protection.
        if not self._breaker.allow():
            raise NanoBananaRetryableError("AI temporarily unavailable; please retry.")

        retries = 0
        while True:
            try:
                response = _call_with_timeout(call_fn, self._timeout_seconds)
                self._breaker.record_success()
                return response
            except Exception as exc:
                retryable = _is_retryable_error(exc)
                self._breaker.record_failure()
                if not retryable:
                    raise NanoBananaError("AI request failed.") from exc
                if retries >= self._max_retries:
                    raise NanoBananaRetryableError("AI generation failed; please retry.") from exc
                delay = _compute_backoff(self._backoff_base, self._backoff_max, retries)
                retries += 1
                logger.warning(
                    "[NanoBanana] retrying (%s/%s) in %.1fs after error: %s",
                    retries,
                    self._max_retries,
                    delay,
                    exc,
                )
                time.sleep(delay)


def _inline_image_to_png(inline: object) -> Optional[bytes]:
    if not inline:
        return None

    if isinstance(inline, dict):
        mime_type = inline.get("mime_type") or inline.get("mimeType")
        data = inline.get("data")
    else:
        mime_type = getattr(inline, "mime_type", None)
        data = getattr(inline, "data", None)

    if not mime_type or not str(mime_type).startswith("image/"):
        return None
    if not data:
        return None

    if isinstance(data, str):
        try:
            data = base64.b64decode(data)
        except Exception:
            return None

    if mime_type == "image/png":
        return data if isinstance(data, (bytes, bytearray)) else None

    try:
        img = Image.open(io.BytesIO(data))
        if img.mode != "RGB":
            img = img.convert("RGB")
        buffer = io.BytesIO()
        img.save(buffer, "PNG")
        return buffer.getvalue()
    except Exception as exc:
        logger.warning("[NanoBanana] image decode failed: %s", exc)
        return None


def _image_to_png_bytes(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    return buffer.getvalue()


def _downscale_image(img: Image.Image, max_size: int) -> Image.Image:
    if max_size <= 0:
        return img
    if max(img.size) <= max_size:
        return img
    resized = img.copy()
    resized.thumbnail((max_size, max_size), Image.BILINEAR)
    return resized


def _describe_layout(img: Image.Image) -> Optional[str]:
    width, height = img.size
    if not width or not height:
        return None

    bbox = _alpha_bbox(img)
    if bbox is None:
        bbox = _edge_bbox(img)
    if bbox is None:
        return None

    left, top, right, bottom = bbox
    box_w = max(1, right - left)
    box_h = max(1, bottom - top)
    x_center = left + box_w / 2
    y_center = top + box_h / 2

    return (
        "Subject bounds approx {w:.1f}% width, {h:.1f}% height; "
        "center at {cx:.1f}% x, {cy:.1f}% y of canvas. "
        "Preserve this scale and framing."
    ).format(
        w=box_w * 100 / width,
        h=box_h * 100 / height,
        cx=x_center * 100 / width,
        cy=y_center * 100 / height,
    )


def _alpha_bbox(img: Image.Image) -> Optional[tuple[int, int, int, int]]:
    if "A" in img.getbands():
        try:
            alpha = img.getchannel("A")
            return alpha.getbbox()
        except Exception:
            return None
    return None


def _edge_bbox(img: Image.Image) -> Optional[tuple[int, int, int, int]]:
    try:
        edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
        # Threshold to reduce noise from low-contrast edges.
        edges = edges.point(lambda v: 255 if v > 20 else 0)
        return edges.getbbox()
    except Exception:
        return None


def _call_with_timeout(call_fn, timeout_seconds: float):
    # Run the provider call in a thread to enforce a hard timeout.
    if timeout_seconds <= 0:
        return call_fn()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(call_fn)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeout as exc:
            raise TimeoutError("Gemini request timed out.") from exc


def _compute_backoff(base: float, cap: float, attempt: int) -> float:
    if base <= 0:
        return 0.0
    delay = base * (2**attempt)
    delay = min(delay, cap) if cap > 0 else delay
    return delay * random.uniform(0.75, 1.25)


def _is_retryable_error(exc: Exception) -> bool:
    # Treat timeouts, rate limits, and transient 5xx as retryable.
    if isinstance(exc, TimeoutError):
        return True
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code is not None:
        try:
            code_val = int(code)
        except Exception:
            code_val = None
        if code_val in {429, 500, 502, 503, 504}:
            return True
    message = str(exc).lower()
    retry_tokens = [
        "429",
        "503",
        "502",
        "504",
        "resource_exhausted",
        "rate limit",
        "quota",
        "unavailable",
        "overloaded",
        "timeout",
        "deadline",
    ]
    return any(token in message for token in retry_tokens)
