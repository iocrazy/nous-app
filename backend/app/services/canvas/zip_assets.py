"""Canvas asset zip packaging (P2-8 → P2-7 "Download All" server-side zip).

The lightbox's Download-All only fires for multi-image output slots, which
are always generated-media results — so the zip endpoint whitelists exactly
those URLs (``/api/v1/generated-media/{id}/(file|stream|cover)``) and refuses
everything else. This is deliberately narrower than Infinite's arbitrary-URL
packer: no SSRF surface, and every id is scope-checked before its bytes are
read.

Pure helpers live here (URL parsing, name de-duplication); the byte reads +
scope checks + zip streaming stay in the router where the request context is.
"""

from __future__ import annotations

import re
from typing import Optional

# Only generated-media serve URLs qualify (relative, same-origin under the
# Vercel rewrite). file/stream/cover all map to the same row id.
_GENERATED_MEDIA_RE = re.compile(
    r"^/api/v1/generated-media/(\d+)/(?:file|stream|cover)$"
)

MAX_ZIP_ITEMS = 64


def parse_generated_media_id(url: str) -> Optional[int]:
    """Return the generated-media id for a whitelisted URL, else None.

    Anything that is not a bare ``/api/v1/generated-media/{id}/(file|stream|
    cover)`` path — absolute URLs, external hosts, query strings, resource
    files — returns None and is rejected by the caller (no remote fetch).
    """
    if not isinstance(url, str):
        return None
    m = _GENERATED_MEDIA_RE.match(url.strip())
    if not m:
        return None
    return int(m.group(1))


def dedupe_zip_name(name: str, taken: set[str]) -> str:
    """Return a unique archive name, suffixing ``-2``, ``-3`` … on collision.

    Mirrors Infinite's zip naming so a batch with repeated names (or none)
    still produces distinct entries. ``taken`` is mutated with the result.
    """
    base = (name or "file").strip() or "file"
    if base not in taken:
        taken.add(base)
        return base
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    n = 2
    while True:
        candidate = f"{stem}-{n}{('.' + ext) if ext else ''}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
        n += 1
