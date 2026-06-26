"""Parse @agent mentions from a chat message body (PHASE-2)."""

from __future__ import annotations

import re
from typing import Any

_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_-]+)")


def extract_agent_mentions(body: dict[str, Any], known_slugs: set[str]) -> list[str]:
    text = ""
    if isinstance(body, dict):
        raw = body.get("text")
        if isinstance(raw, str):
            text = raw
    if not text or not known_slugs:
        return []
    lower_known = {s.lower(): s for s in known_slugs}
    out: list[str] = []
    seen: set[str] = set()
    for m in _MENTION_RE.finditer(text):
        slug = lower_known.get(m.group(1).lower())
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out
