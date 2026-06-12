# backend/app/services/ai/classify/normalize.py

"""Normalization for the asset-classification agent output.

Port of Infinite-Canvas ``normalize_asset_classification`` (main.py:4858),
adapted to MediaHub's bilingual tag design: the agent emits
``{"dimensions": {key: [{"en", "zh"}, ...]}}`` and this module flattens it
into clean ``(dimension, group, en, zh)`` entries ready for the tags
write-through (find-or-create tag + tag_group).

IC's 15 dimensions are deduped to 12 (scene≈environment, model≈people
merged; the free-form objects/tags buckets dropped — they produce noisy
one-off tags that don't help filtering).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List

# dimension key (agent output) → tag_groups.name (UI label, English per
# the UI-language convention; tags themselves are bilingual name/name_zh).
DIMENSIONS: Dict[str, str] = {
    "environment": "Environment",
    "space": "Space",
    "subject": "Subject",
    "people": "People",
    "style": "Style",
    "lighting": "Lighting",
    "color": "Color",
    "composition": "Composition",
    "mood": "Mood",
    "use_case": "Use Case",
    "materials": "Materials",
    "quality": "Quality",
}

# Per-dimension tag cap — IC capped at 8; 6 keeps the tag cloud usable.
MAX_TAGS_PER_DIMENSION = 6
# tags.name is VARCHAR(50); IC trimmed to 24 which also keeps chips compact.
MAX_TAG_CHARS = 24

_WS_RE = re.compile(r"\s+")
_LEAD_HASH_RE = re.compile(r"^[#＃]+")
_EDGE_SEPARATORS = " ,，、;；|/"


def sanitize_tag(value: Any, limit: int = MAX_TAG_CHARS) -> str:
    """Clean a single tag label (IC ``_safe_asset_tag`` port)."""
    text = _WS_RE.sub(" ", str(value or "").strip())
    text = _LEAD_HASH_RE.sub("", text).strip(_EDGE_SEPARATORS)
    return text[:limit]


@dataclass(frozen=True)
class ClassifiedTag:
    """One normalized classification tag, ready for the tags write-through."""

    dimension: str
    group: str
    en: str
    zh: str


def normalize_classification(raw: Any) -> List[ClassifiedTag]:
    """Flatten + clean the agent's classification payload.

    Keeps only known dimensions, requires a non-empty ``en`` side
    (canonical tag name), dedups case-insensitively per dimension, and
    caps each dimension. Unknown shapes degrade to an empty list —
    callers treat that as "the model returned nothing usable".
    """
    if not isinstance(raw, dict):
        return []
    dimensions = raw.get("dimensions")
    if not isinstance(dimensions, dict):
        return []

    out: List[ClassifiedTag] = []
    for key, values in dimensions.items():
        norm_key = str(key or "").strip().lower()
        group = DIMENSIONS.get(norm_key)
        if not group or not isinstance(values, list):
            continue
        seen: set[str] = set()
        kept = 0
        for value in values:
            if not isinstance(value, dict):
                continue
            en_raw = value.get("en")
            zh_raw = value.get("zh")
            en = sanitize_tag(en_raw) if isinstance(en_raw, str) else ""
            zh = sanitize_tag(zh_raw) if isinstance(zh_raw, str) else ""
            if not en:
                continue
            dedup_key = en.lower()
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            out.append(ClassifiedTag(dimension=norm_key, group=group, en=en, zh=zh))
            kept += 1
            if kept >= MAX_TAGS_PER_DIMENSION:
                break
    return out
