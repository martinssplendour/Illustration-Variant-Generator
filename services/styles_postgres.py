"""Postgres-backed style catalog and rule formatting."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg
from psycopg.rows import dict_row

from .timing import log_timing

logger = logging.getLogger(__name__)

MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}


@dataclass(frozen=True)
class StyleSummary:
    style_id: str
    name: str


@dataclass(frozen=True)
class StyleRecord(StyleSummary):
    rules_text: str
    reference_bytes: bytes
    reference_mime: str
    style_profile: Optional[dict]


class PostgresStyleCatalog:
    def __init__(self, dsn: str, max_rules_chars: int) -> None:
        self._dsn = dsn
        self._max_rules_chars = max_rules_chars

    def list_styles(self) -> list[StyleSummary]:
        with log_timing("db list_styles", logger):
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                rows = conn.execute(
                    "SELECT style_id, style_name FROM styles ORDER BY style_name ASC"
                ).fetchall()
        return [StyleSummary(style_id=row["style_id"], name=row["style_name"]) for row in rows]

    def get_style(self, style_id: str) -> Optional[StyleRecord]:
        safe_id = Path(style_id).name
        if safe_id != style_id:
            return None

        with log_timing(f"db get_style {safe_id}", logger):
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                row = conn.execute(
                    """
                    SELECT style_id, style_name, rules_text, reference_image, reference_mime, style_profile
                    FROM styles
                    WHERE style_id = %s
                    """,
                    (safe_id,),
                ).fetchone()

        if not row:
            return None

        rules_text = row["rules_text"] or ""
        if self._max_rules_chars > 0 and len(rules_text) > self._max_rules_chars:
            # Truncate long style guides to keep prompts bounded.
            rules_text = rules_text[: self._max_rules_chars].rstrip()

        style_profile = row.get("style_profile")
        if style_profile:
            if isinstance(style_profile, str):
                try:
                    # style_profile may be stored as JSON text.
                    style_profile = json.loads(style_profile)
                except json.JSONDecodeError:
                    style_profile = None
            elif not isinstance(style_profile, dict):
                style_profile = None

        return StyleRecord(
            style_id=row["style_id"],
            name=row["style_name"],
            rules_text=rules_text,
            reference_bytes=row["reference_image"],
            reference_mime=row["reference_mime"],
            style_profile=style_profile,
        )

    def load_rules(self, style: StyleRecord) -> str:
        if style.style_profile:
            return _format_style_profile(style.style_profile)
        return _format_rules_text(style.rules_text)

    def materialize_reference(self, style: StyleRecord, output_dir: Path) -> Path:
        safe_id = Path(style.style_id).name
        ext = MIME_EXTENSIONS.get(style.reference_mime, ".png")
        output_path = output_dir / f"style_{safe_id}{ext}"
        if output_path.is_file():
            return output_path

        if not style.reference_bytes:
            return output_path

        try:
            output_path.write_bytes(style.reference_bytes)
        except OSError as exc:
            logger.warning("Failed to write style reference %s: %s", style.style_id, exc)
        return output_path


def _format_style_profile(profile: dict) -> str:
    summary_lines = _summarize_profile(profile)
    if summary_lines:
        return "STYLE_PROFILE_SUMMARY:\n" + "\n".join(summary_lines)
    return "STYLE_PROFILE_JSON:\n" + json.dumps(profile, indent=2, ensure_ascii=True)


def _summarize_profile(profile: dict) -> list[str]:
    if not isinstance(profile, dict):
        return []

    lines: list[str] = []
    tech = _as_dict(profile.get("technical_specifications"))

    brush = _as_dict(tech.get("brush_settings") or profile.get("brush_settings"))
    brush_text = _summarize_dict(
        brush,
        ("type", "size", "dynamics", "quality", "consistency", "consistency_rule"),
    )
    if brush_text:
        lines.append(f"- Brush: {brush_text}")

    lineart = _as_dict(tech.get("lineart_rules") or profile.get("lineart_rules"))
    lineart_text = _summarize_dict(
        lineart,
        (
            "continuity",
            "line_weight",
            "line_quality",
            "coloured_lines",
            "colored_lines",
            "no_lines_color_version",
        ),
    )
    if lineart_text:
        lines.append(f"- Lineart: {lineart_text}")

    shading = _as_dict(
        tech.get("shading_rules")
        or tech.get("shading")
        or profile.get("shading_rules")
        or profile.get("shading")
    )
    shading_text = _summarize_dict(
        shading,
        ("technique", "placement", "opacity", "color", "light_source", "layers", "scope"),
    )
    if shading_text:
        lines.append(f"- Shading: {shading_text}")

    lighting = _summarize_value(tech.get("lighting") or profile.get("lighting"))
    if lighting:
        lines.append(f"- Lighting: {lighting}")

    color_profiles = _as_dict(tech.get("color_profiles") or profile.get("color_profiles"))
    colors_text = _summarize_color_profiles(color_profiles)
    if colors_text:
        lines.append(f"- Line colors: {colors_text}")

    return lines


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _summarize_dict(value: dict, keys: tuple[str, ...]) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    parts: list[str] = []
    for key in keys:
        if key in value and value[key]:
            parts.append(f"{_clean_label(key)}: {_summarize_value(value[key])}")
    if not parts:
        for key, raw in list(value.items())[:3]:
            parts.append(f"{_clean_label(key)}: {_summarize_value(raw)}")
    return "; ".join(parts)


def _summarize_color_profiles(color_profiles: dict) -> str:
    if not isinstance(color_profiles, dict) or not color_profiles:
        return ""
    parts: list[str] = []
    colors = color_profiles.get("colors")
    if isinstance(colors, list):
        for item in colors[:3]:
            if not isinstance(item, dict):
                continue
            parts.append(_format_color_entry(item))
        return "; ".join([part for part in parts if part])

    for key, item in color_profiles.items():
        if not isinstance(item, dict):
            continue
        entry = _format_color_entry(item)
        if entry:
            parts.append(entry)
    return "; ".join(parts[:3])


def _format_color_entry(item: dict) -> str:
    name = _summarize_value(item.get("name"))
    usage = _summarize_value(item.get("usage"))
    hex_value = _summarize_value(item.get("hex"))
    cmyk = item.get("cmyk") or item.get("cmyk_values")
    cmyk_value = _summarize_value(cmyk)
    parts = [part for part in (name, hex_value, cmyk_value) if part]
    if usage:
        parts.append(f"usage: {usage}")
    return " ".join(parts).strip()


def _summarize_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        items = []
        for key, raw in list(value.items())[:3]:
            items.append(f"{_clean_label(key)}: {_summarize_value(raw)}")
        return _clean_text(", ".join(items))
    if isinstance(value, list):
        return _clean_text(", ".join(str(item) for item in value[:4]))
    return _clean_text(value)


def _clean_label(label: str) -> str:
    return str(label).replace("_", " ").strip()


def _clean_text(value: object, max_len: int = 220) -> str:
    text = " ".join(str(value).split())
    if len(text) > max_len:
        return text[: max_len - 3].rstrip() + "..."
    return text


def _format_rules_text(text: str) -> str:
    if not text:
        return ""
    extracted = _extract_json_blocks(text)
    if not extracted:
        return text.strip()

    json_blocks = []
    spans: list[tuple[int, int]] = []
    for obj, start, end in extracted:
        json_blocks.append(json.dumps(obj, indent=2, ensure_ascii=True))
        spans.append((start, end))

    cleaned = _remove_spans(text, spans).strip()
    parts: list[str] = []
    if json_blocks:
        parts.append("JSON_RULES:\n" + "\n\n".join(json_blocks))
    if cleaned:
        parts.append("TEXT_RULES:\n" + cleaned)
    return "\n\n".join(parts).strip()


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    spans = sorted(spans, key=lambda item: item[0])
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        if cursor < start:
            parts.append(text[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(text):
        parts.append(text[cursor:])
    return "".join(parts)


def _extract_json_blocks(text: str) -> list[tuple[object, int, int]]:
    results: list[tuple[object, int, int]] = []
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)

    while idx < length:
        if text[idx] not in "{[":
            idx += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, idx)
        except Exception:
            idx += 1
            continue
        results.append((obj, idx, end))
        idx = end

    return results
