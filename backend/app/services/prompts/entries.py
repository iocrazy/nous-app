"""Pure builders: one storage row → one PromptEntry dict (spec §3.1).

No I/O. The repository hands in plain dicts; the service hands the results to
Pydantic. Keeping the mapping here means the resource-library page and the
canvas panel can never disagree about what a row's title or thumbnail is.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .origin import is_blank_text

_THUMB_CAP = 3


def _text(value: Any) -> Optional[str]:
    """Blank sentinels render as missing; ``origin.is_blank_text`` owns the rule."""
    return None if is_blank_text(value) else value


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def title_from_filename(name: Optional[str]) -> str:
    stem = os.path.splitext(name or "")[0].strip()
    return stem or "Untitled"


def _cover(resource_id: Any) -> Dict[str, str]:
    return {"url": f"/api/v1/resources/{resource_id}/cover", "kind": "image"}


def entry_from_asset(
    row: Mapping[str, Any], *, example_resource_ids: Iterable[Any]
) -> Dict[str, Any]:
    """A prompt asset → template entry. Thumbs: cover first, then example files."""
    thumbs: List[Dict[str, str]] = []
    if row.get("cover_file_id"):
        thumbs.append(_cover(row["cover_file_id"]))
    for rid in example_resource_ids:
        if len(thumbs) >= _THUMB_CAP:
            break
        if str(rid) != str(row.get("cover_file_id")):
            thumbs.append(_cover(rid))
    tags: List[str] = []
    for values in (row.get("tags") or {}).values():
        if isinstance(values, list):
            tags.extend(str(v) for v in values)
        elif isinstance(values, str):
            tags.append(values)
    return {
        "key": f"template:{row['id']}",
        "form": "template",
        "origin": "typed",
        "title": row.get("name") or "Untitled",
        "tags": tags,
        "positive_en": _text(row.get("prompt_positive")),
        "positive_zh": _text(row.get("prompt_positive_zh")),
        "negative_en": _text(row.get("prompt_negative")),
        "negative_zh": _text(row.get("prompt_negative_zh")),
        "params": (row.get("platform_params") or None),
        "thumbs": thumbs,
        "slides": None,
        "source": {"store": "assets", "id": str(row["id"])},
        "updated_at": _iso(row.get("updated_at")),
    }


def _slides(row: Mapping[str, Any]) -> List[Dict[str, Any]]:
    media_id = row.get("media_id")
    out: List[Dict[str, Any]] = []
    for name in sorted((row.get("slide_prompts") or {}).keys()):
        entry = row["slide_prompts"].get(name) or {}
        if not isinstance(entry, dict):
            entry = {}
        out.append(
            {
                "name": name,
                "url": f"/api/v1/media/{media_id}/slides/{name}" if media_id else None,
                "positive_en": _text(entry.get("en")),
                "positive_zh": _text(entry.get("zh")),
                "negative_en": _text(entry.get("neg_en")),
                "negative_zh": _text(entry.get("neg_zh")),
            }
        )
    return out


def entry_from_resource(row: Mapping[str, Any]) -> Dict[str, Any]:
    """A prompted resource → image entry, or album entry when slide_prompts is non-empty."""
    slide_map = row.get("slide_prompts")
    is_album = isinstance(slide_map, dict) and len(slide_map) > 0
    rid = row["id"]
    base = {
        "origin": row.get("prompt_origin"),
        "title": title_from_filename(row.get("filename")),
        "tags": [],
        "params": (row.get("gen_params") or None),
        "source": {"store": "uploads", "id": str(rid)},
        "updated_at": _iso(row.get("updated_at")),
    }
    if not is_album:
        return {
            **base,
            "key": f"image:{rid}",
            "form": "image",
            "positive_en": _text(row.get("gen_prompt")),
            "positive_zh": _text(row.get("gen_prompt_zh")),
            "negative_en": _text(row.get("gen_prompt_negative")),
            "negative_zh": _text(row.get("gen_prompt_negative_zh")),
            "thumbs": [_cover(rid)],
            "slides": None,
        }
    slides = _slides(row)
    first = slides[0] if slides else {}
    return {
        **base,
        "key": f"album:{rid}",
        "form": "album",
        # The album's own line is its first slide's text: the row-level
        # gen_prompt is a caption of the cover at best, never the slide's.
        "positive_en": first.get("positive_en"),
        "positive_zh": first.get("positive_zh"),
        "negative_en": first.get("negative_en"),
        "negative_zh": first.get("negative_zh"),
        "thumbs": [
            {"url": s["url"], "kind": "image"} for s in slides[:_THUMB_CAP] if s["url"]
        ],
        "slides": slides,
    }


def matches_query(entry: Mapping[str, Any], q: str) -> bool:
    needle = (q or "").strip().lower()
    if not needle:
        return True
    hay = [
        entry.get("title") or "",
        entry.get("positive_en") or "",
        entry.get("positive_zh") or "",
    ]
    hay.extend(entry.get("tags") or [])
    for s in entry.get("slides") or []:
        hay.extend([s.get("positive_en") or "", s.get("positive_zh") or ""])
    return any(needle in str(h).lower() for h in hay)


def _ts(value: Any) -> float:
    """ISO-8601 → sortable float; unparsable → 0 (sorts last within its rank)."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def sort_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Captioned (and unlabelled) rows after every authored one; recent first within."""

    def rank(e: Mapping[str, Any]) -> int:
        return 1 if e.get("origin") in ("captioned", None) else 0

    return sorted(entries, key=lambda e: (rank(e), -_ts(e.get("updated_at"))))
