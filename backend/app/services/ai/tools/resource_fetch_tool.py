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


def _contained_doc_path(fp: str) -> Optional[Path]:
    """Resolve a resource ``file_path`` under DOWNLOAD_PATH, or None if it
    escapes (audit #20).

    ``fp`` comes from an already-ownership-validated DB row, not agent input,
    so this is defense-in-depth: a buggy/malicious upload import that stored a
    ``../``-containing path must not let a doc read escape the downloads dir.
    """
    download_root = Path(os.environ.get("DOWNLOAD_PATH", "/app/downloads")).resolve()
    candidate = (download_root / fp).resolve()
    if not candidate.is_relative_to(download_root):
        return None
    return candidate


async def _fetch_dispatch(
    *,
    resource_id: str,
    mode: str | None,
    args: dict | None,
    user_id: str,
    team_id: int | None = None,
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

    ``team_id`` (CHAT-SEC-AGENT-03): when set, additionally constrains the
    query to resources whose ``ri.scope_id`` equals the channel's team.
    ``team_id=None`` preserves the existing behaviour for the ai_library path.
    """
    from contextlib import nullcontext

    from sqlalchemy import String, cast, select

    from app.db.scope import is_enforced, system_request_scope
    from app.db.session import read_scope
    from app.models import (
        ResourceItems,
        Resources,
        ResourceSummaries,
        ResourceTranscripts,
        TeamMembers,
    )

    # Original SQL (kept for reference — same JOIN/WHERE/LIMIT shape):
    #   SELECT r.id::text, r.mime_type AS mime, r.filename AS name,
    #          r.file_path, r.notes AS brief
    #     FROM public.resources r
    #     JOIN public.resource_items ri ON ri.resource_id = r.id
    #    WHERE r.id::text = :rid AND r.is_trashed = false
    #      AND ri.scope_id::text IN (
    #            SELECT team_id::text FROM public.team_members WHERE user_id=:uid
    #          )
    #      [AND ri.scope_id::text = :tid::text]  -- when team_id is not None
    #    LIMIT 1
    id_text = cast(Resources.id, String)
    scope_text = cast(ResourceItems.scope_id, String)
    membership_subq = select(cast(TeamMembers.team_id, String)).where(
        TeamMembers.user_id == user_id
    )

    stmt = (
        select(
            id_text.label("id"),
            Resources.mime_type.label("mime"),
            Resources.filename.label("name"),
            Resources.file_path,
            Resources.notes.label("brief"),
        )
        .join(ResourceItems, ResourceItems.resource_id == Resources.id)
        .where(id_text == resource_id)
        .where(Resources.is_trashed.is_(False))
        # After Spec 1 PR-C: ri.scope_id is always a teams.id snowflake;
        # personal scope is a single-member team containing the user.
        .where(scope_text.in_(membership_subq))
    )
    if team_id is not None:
        # CHAT-SEC-AGENT-03: additionally constrain to the channel's team.
        stmt = stmt.where(scope_text == str(int(team_id)))
    stmt = stmt.limit(1)

    # Resources carries UserScoped(creator_id), but THIS access check is
    # governed by team membership, not creator_id — a resource shared to a
    # team the caller belongs to must stay visible even when not owned by
    # them. Forcing SYSTEM scope keeps this query correct (rather than
    # silently creator_id-filtered). SCOPE_ENFORCE_RESOURCES DEFAULTS to
    # false in code, but production sets it TRUE via secrets/backend.env
    # (outside this repo tree — CLAUDE.md's 部署陷阱 on env overriding
    # config.yml): in production this wrap is LOAD-BEARING — without it the
    # do_orm_execute choke point sees Resources touched with no ambient
    # scope and fail-closed raises UnscopedQueryError, turning every
    # ResourceFetch call into a 500 instead of a clean PermissionError. The
    # is_enforced gate exists only to stay byte-for-byte legacy where the
    # flag genuinely is off (e.g. this repo's local/test default).
    scope_cm = (
        system_request_scope(reason="resource-fetch-team-membership-access")
        if is_enforced("resources")
        else nullcontext()
    )
    async with scope_cm:
        async with read_scope() as session:
            row = (await session.execute(stmt)).mappings().first()

    if not row:
        raise PermissionError(f"resource {resource_id} not accessible to {user_id}")

    row = dict(row)
    mime = (row.get("mime") or "").lower()

    # Image — resolve the actual BYTES and inline them as a data URL.
    # The pre-2026-07-30 shape minted a relative /api/v1/media/{id}?token=
    # URL, which no external provider can fetch — and even if it could,
    # the result rides in a role:tool message where vision pipelines never
    # look. AgentRunner promotes data-URL blocks from this result into a
    # user-message image part (see agent_runner image promotion), so the
    # model actually sees the pixels. resolve_attachments handles all
    # three file_path shapes (sb:// object store / shared-volume relative /
    # public http) with the size cap and S3 timeout guards already proven
    # on the chat-attachment path.
    if mime.startswith("image/"):
        fp = row.get("file_path") or ""
        if not fp:
            return {"error": f"image resource {row['name']!r} has no file_path"}

        from app.schemas.ai_library_chat import AttachmentRequest
        from app.services.ai.chat.chat_attachment_resolver import (
            resolve_attachments,
        )

        resolved = await resolve_attachments(
            [AttachmentRequest(kind="image", url=fp, mime=mime)]
        )
        if not resolved.attachments:
            reason = resolved.failures[0].reason if resolved.failures else "unknown"
            logger.warning(
                f"[resource_fetch] image {resource_id} unresolvable: {reason}"
            )
            return {"error": (f"image {row['name']!r} could not be loaded: {reason}")}
        att = resolved.attachments[0]
        return {
            "content": [
                {
                    "type": "image_url",
                    "url": att.data_url or att.url,
                    "mime": mime,
                }
            ],
            "meta": {"name": row["name"], "kind": "image"},
        }

    # Video / audio — AI-generated text lives on resource_summaries /
    # resource_transcripts, keyed by resource_id (UNIQUE, at most one row
    # each). The pre-2026-08 implementation joined `public.videos`, a table
    # renamed away by migration 066 — every video/audio fetch raised and
    # degraded to "fetch failed" for months. Access is already validated by
    # the resources+resource_items team-membership check above; these two
    # tables carry no scope mixin, so no wrapper is needed here. The int()
    # bind is required: resource_id arrives as str and asyncpg's int8 codec
    # rejects str for BIGINT columns.
    if mime.startswith("video/") or mime.startswith("audio/"):
        m = mode or ("summary" if mime.startswith("video/") else "transcript")
        rid = int(resource_id)
        if m == "summary":
            async with read_scope() as session:
                text = (
                    await session.execute(
                        select(ResourceSummaries.summary_text).where(
                            ResourceSummaries.resource_id == rid
                        )
                    )
                ).scalar_one_or_none()
            if not text:
                return {"error": "summary not available; resource not yet processed"}
            return {"content": text, "meta": {"name": row["name"], "mode": "summary"}}
        if m == "transcript":
            async with read_scope() as session:
                text = (
                    await session.execute(
                        select(ResourceTranscripts.full_text).where(
                            ResourceTranscripts.resource_id == rid
                        )
                    )
                ).scalar_one_or_none()
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
    # Audit #20: defense-in-depth path containment (see _contained_doc_path).
    abs_path = _contained_doc_path(fp)
    if abs_path is None:
        logger.warning(
            f"[resource_fetch] path escape blocked: resource file_path={fp!r} "
            f"resolved outside download root"
        )
        return {"error": "resource path is outside the allowed directory"}
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
    team_id: Optional[int] = None,
) -> dict[str, Any]:
    """Public entry point — the runtime registers this as the tool callable.

    ``available_refs`` is the set of resource ids that appeared in
    ``<available_resources>`` for this turn. Calling the tool with an id
    outside that set returns an error — agents must reference what the
    user gave them, not arbitrary ids.

    ``team_id`` (CHAT-SEC-AGENT-03): when set, limits access to resources
    belonging to the given channel team (``ri.scope_id = team_id``). Callers
    that do not pass this arg get the original membership-only check.
    """
    rid = str(resource_id)
    if rid not in available_refs:
        return {"error": "resource not referenced in this turn"}

    args_hash = hashlib.sha1(
        json.dumps(args or {}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    cache_key = (rid, mode or "_default_", args_hash, team_id)
    if cache_key in request_cache:
        return request_cache[cache_key]

    try:
        result = await _fetch_dispatch(
            resource_id=rid,
            mode=mode,
            args=args,
            user_id=user_id,
            team_id=team_id,
        )
    except PermissionError as exc:
        logger.info(f"[resource_fetch] permission denied: {exc!r}")
        return {"error": "resource not accessible"}
    except Exception as exc:
        logger.exception(f"[resource_fetch] dispatch failed: {exc!r}")
        return {"error": f"fetch failed: {exc.__class__.__name__}"}

    request_cache[cache_key] = result
    return result
