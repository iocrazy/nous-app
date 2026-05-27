"""ResourceFetch tool — agents call this to load content for a
``@``-referenced resource.

Mirrors the lazy ``Skill(slug, file)`` pattern: ``<available_resources>``
in the system message gives the agent metadata; the agent calls
``ResourceFetch(id, mode?)`` only when it actually needs the content.

Returns ``{"content": ...}`` on success, ``{"error": "<short>"}`` on any
failure — never raises (the agent must see the error and decide).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from loguru import logger


async def _fetch_dispatch(
    *, resource_id: str, mode: str | None, args: dict | None, user_id: str
) -> dict[str, Any]:
    """Per-kind dispatch. Raises PermissionError if the user can't read
    the resource at fetch time (defends against scope drift between
    ``send_user_message`` and the agent's tool call).

    v1 mode coverage:
      - image:   returns {content: [{type:'image_url', url, mime}]}
      - video:   summary (default) / transcript / frames (frames=not yet)
      - audio:   transcript (default)
      - doc:     excerpt (default; first 4000 chars) / full (64k char cap)
      - pdf:     not yet implemented in v1
    """
    from app.db import engine as db_engine

    rows = await db_engine.fetch_all(
        """
        SELECT r.id::text, r.mime_type AS mime, r.filename AS name,
               r.file_path, r.description AS brief
          FROM public.resources r
          JOIN public.resource_items ri ON ri.resource_id = r.id
         WHERE r.id::text = :rid
           AND r.is_trashed = false AND ri.is_trashed = false
           AND ( (ri.scope_type='personal' AND ri.scope_id::text=:uid)
                 OR (ri.scope_type='team' AND ri.scope_id IN (
                      SELECT team_id FROM public.team_members WHERE user_id=:uid
                 )) )
         LIMIT 1
        """,
        {"rid": resource_id, "uid": user_id},
    )
    if not rows:
        raise PermissionError(f"resource {resource_id} not accessible to {user_id}")

    row = rows[0]
    mime = (row.get("mime") or "").lower()

    # Image
    if mime.startswith("image/"):
        try:
            from app.api.media_auth import _sign_token as _sign
            import time

            now = int(time.time())
            expires_at = now + 4 * 3600
            token = _sign(user_id, now, expires_at)
            url = f"/api/v1/media/{resource_id}?token={token}"
        except Exception as exc:
            logger.warning(f"[resource_fetch] failed to mint media token: {exc!r}")
            url = f"/api/v1/media/{resource_id}"
        return {
            "content": [{"type": "image_url", "url": url, "mime": mime}],
            "meta": {"name": row["name"], "kind": "image"},
        }

    # Video / audio — read from `videos` table joined on parsed_media
    if mime.startswith("video/") or mime.startswith("audio/"):
        m = mode or ("summary" if mime.startswith("video/") else "transcript")
        media_rows = await db_engine.fetch_all(
            """
            SELECT v.summary, v.transcript
              FROM public.videos v
              JOIN public.parsed_media pm ON pm.id = v.parsed_media_id
              JOIN public.resources r ON r.media_id = pm.id
             WHERE r.id::text = :rid
             LIMIT 1
            """,
            {"rid": resource_id},
        )
        v = (media_rows or [{}])[0]
        if m == "summary":
            text = v.get("summary")
            if not text:
                return {"error": "summary not available; resource not yet processed"}
            return {"content": text, "meta": {"name": row["name"], "mode": "summary"}}
        if m == "transcript":
            text = v.get("transcript")
            if not text:
                return {"error": "transcript not available; resource not yet processed"}
            return {
                "content": text,
                "meta": {"name": row["name"], "mode": "transcript"},
            }
        if m == "frames":
            return {
                "error": "mode='frames' not yet implemented in v1; use summary or transcript"
            }
        return {"error": f"unknown mode {m!r} for video/audio resource"}

    # PDF / doc — read file content from disk via the resource path
    fp = row.get("file_path") or ""
    if not fp:
        return {"error": "resource has no file_path on disk"}
    abs_path = Path(os.environ.get("DOWNLOAD_PATH", "/app/downloads")) / fp
    if not abs_path.exists():
        return {"error": f"file missing on disk: {abs_path.name}"}

    if mime == "application/pdf":
        return {"error": "mode='page'/'excerpt' for PDF not yet implemented in v1"}

    # Treat everything else as text doc
    m = mode or "excerpt"
    try:
        raw = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"error": f"failed to read file: {exc!r}"}

    if m == "excerpt":
        excerpt = raw[:4000]
        suffix = ""
        if len(raw) > 4000:
            suffix = (
                f"\n[... truncated, {len(raw) - 4000} bytes remaining; "
                "use mode='full' for the entire document]"
            )
        return {
            "content": excerpt + suffix,
            "meta": {"name": row["name"], "mode": "excerpt", "bytes": len(raw)},
        }
    if m == "full":
        cap = 64_000
        if len(raw) > cap:
            return {
                "content": (
                    raw[:cap] + f"\n[... truncated, {len(raw) - cap} bytes remaining]"
                ),
                "meta": {
                    "name": row["name"],
                    "mode": "full",
                    "bytes": len(raw),
                    "truncated": True,
                },
            }
        return {
            "content": raw,
            "meta": {"name": row["name"], "mode": "full", "bytes": len(raw)},
        }
    return {"error": f"unknown mode {m!r} for doc resource"}


async def resource_fetch(
    *,
    resource_id: str,
    mode: Optional[str] = None,
    args: Optional[dict] = None,
    user_id: str,
    available_refs: set[str],
    request_cache: dict,
) -> dict[str, Any]:
    """Public entry point — the runtime registers this as the tool callable.

    ``available_refs`` is the set of resource ids that appeared in
    ``<available_resources>`` for this turn. Calling the tool with an id
    outside that set returns an error — agents must reference what the
    user gave them, not arbitrary ids.
    """
    rid = str(resource_id)
    if rid not in available_refs:
        return {"error": "resource not referenced in this turn"}

    args_hash = hashlib.sha1(
        json.dumps(args or {}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    cache_key = (rid, mode or "_default_", args_hash)
    if cache_key in request_cache:
        return request_cache[cache_key]

    try:
        result = await _fetch_dispatch(
            resource_id=rid,
            mode=mode,
            args=args,
            user_id=user_id,
        )
    except PermissionError as exc:
        logger.info(f"[resource_fetch] permission denied: {exc!r}")
        return {"error": "resource not accessible"}
    except Exception as exc:
        logger.exception(f"[resource_fetch] dispatch failed: {exc!r}")
        return {"error": f"fetch failed: {exc.__class__.__name__}"}

    request_cache[cache_key] = result
    return result
