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

from app.services.ai.resource_ai_status import effective_ai_statuses

# ── mode='frames' budget ─────────────────────────────────────────────
# Every number here is a byte budget with a named source of truth, not a
# "reasonable default". The frames ride into the request as base64 in a
# promoted user message (see agent_runner's image promotion), so an
# unbounded frame count or an unbounded per-frame size is an unbounded
# request body.
#
# Default count mirrors chat_attachment_resolver.MAX_VIDEO_FRAMES_PER_ATTACHMENT:
# @-mentioning a video and attaching one must show the model the same
# number of frames, or the same question gets two different answers
# depending on how the video arrived. The ceiling mirrors
# cover_frames.MAX_COVER_FRAMES — the other place that samples a
# user-chosen number of frames off one video.
#
# Both are pinned by tests/test_resource_fetch_frames.py (which imports
# the two source modules and asserts equality) rather than imported here:
# cover_frames drags in PIL plus the canvas persistence graph, and this
# tool sits on the chat hot path. The test is what makes the coupling
# falsifiable — drift turns it red instead of silently diverging.
FRAMES_DEFAULT_COUNT = 6
FRAMES_MAX_COUNT = 12

# Per-frame ceiling for the inlined JPEG. Measured against real ffmpeg at
# the extractor's defaults (640 px wide, -q:v 4), as RAW JPEG bytes:
# ordinary footage 16-19 KB, SMPTE bars 18 KB, and a full-frame
# random-noise source — the pathological worst case an encoder can hand
# ffmpeg — 85 KB (110 KB once base64'd into the data URL). 200 KB is
# therefore a backstop real content does not reach, and it turns the
# worst case into a CONSTRUCTION guarantee rather than an expectation:
# FRAMES_MAX_COUNT x this = 2.4 MB raw (~3.2 MB base64) for the largest
# call a model can ask for. Observed end to end: 12 frames of ordinary
# footage 0.25-0.29 MB, of pure noise 1.29 MB. For comparison, one chat
# image attachment may inline up to MAX_INLINE_IMAGE_BYTES = 10 MB.
# Frames over the cap are dropped AND counted into the result's
# ``warning`` — never silently.
FRAMES_MAX_BYTES_PER_FRAME = 200 * 1024
# The data URL carries base64 (4 chars per 3 bytes) plus the
# "data:image/jpeg;base64," header.
_FRAMES_MAX_DATA_URL_CHARS = (FRAMES_MAX_BYTES_PER_FRAME * 4 + 2) // 3 + 32

# Two bounds, same shape as cover_frames' pair but far tighter: this runs
# inside a live chat turn with the user watching a stream, not in a
# background workflow (cover_frames allows 180 s / 600 s). ffmpeg's own
# budget is divided across the frames by extract_frames; the outer
# deadline additionally covers materialize()'s download, which has no
# timeout of its own.
FRAMES_EXTRACT_TIMEOUT_SECONDS = 90.0
FRAMES_TOTAL_DEADLINE_SECONDS = 180.0


def _parse_frame_count(args: Optional[dict]) -> tuple[Optional[int], Optional[str]]:
    """``args={"frames": N}`` → a clamped count, or a typed error string.

    Out-of-range clamps rather than rejects: a model asking for 40 frames
    is saying "as many as you can", and the budget above is the real
    answer. A non-numeric value is rejected instead, because silently
    substituting the default there would answer a question the model did
    not ask and give it no way to notice.
    """
    raw = (args or {}).get("frames")
    if raw is None:
        return FRAMES_DEFAULT_COUNT, None
    bad = f"frames must be a whole number, got {raw!r}"
    if isinstance(raw, bool):
        # bool is an int subclass; True would silently mean "1 frame".
        return None, bad
    if isinstance(raw, int):
        n = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            return None, bad
        n = int(raw)
    elif isinstance(raw, str):
        try:
            n = int(raw.strip())
        except ValueError:
            return None, bad
    else:
        return None, bad
    return max(1, min(FRAMES_MAX_COUNT, n)), None


async def _video_frames(
    *, file_path: str, num_frames: int, name: str
) -> dict[str, Any]:
    """Sample ``num_frames`` frames off a video and return them as
    ``image_url`` blocks in the same shape the image branch returns.

    ffmpeg argv comes from ``seek_frame_cmd`` via ``extract_frames`` — the
    single argv builder the cover-frame path is also pinned to — so the
    uniform sampling rule (first/last 5% trimmed) is shared rather than
    re-derived here.

    Per-frame failures are NOT swallowed: ``extract_frames`` degrades a
    failed frame to "one fewer attachment", which on its own is
    indistinguishable from a short video. Whatever is missing is counted
    and reported back in ``warning`` (partial success) or as a typed
    ``error`` (nothing usable) — the agent must be able to tell the user
    it is looking at 4 frames when it asked for 6.
    """
    import asyncio

    from app.services.library.media_storage import materialize
    from app.services.media.render.video_frame_extractor import (
        DEFAULT_FRAME_WIDTH,
        extract_frames,
    )

    try:
        # One outer bound over both halves — the download inside
        # materialize() and the ffmpeg run. Timeout surfaces as
        # CancelledError inside the async with body, so materialize's
        # finally (temp file) and extract_frames' TemporaryDirectory both
        # still run; nothing is left behind.
        async with asyncio.timeout(FRAMES_TOTAL_DEADLINE_SECONDS):
            async with materialize(file_path) as local_path:
                if not local_path.exists():
                    return {
                        "error": (
                            f"video file not available for frame extraction: "
                            f"{name!r} is not on disk"
                        )
                    }
                result = await extract_frames(
                    str(local_path),
                    num_frames=num_frames,
                    frame_width=DEFAULT_FRAME_WIDTH,
                    timeout_seconds=FRAMES_EXTRACT_TIMEOUT_SECONDS,
                )
    except TimeoutError:
        # asyncio.timeout raises the builtin TimeoutError (3.11+). Must be
        # caught before the Exception arm below.
        return {
            "error": (
                f"frame extraction timed out after "
                f"{FRAMES_TOTAL_DEADLINE_SECONDS:.0f}s"
            )
        }
    except ValueError as exc:
        # materialize's containment guard (a file_path escaping DOWNLOAD_PATH).
        logger.warning(f"[resource_fetch] frames path rejected for {name!r}: {exc}")
        return {"error": "video file path is outside the allowed directory"}
    except Exception as exc:
        # Storage errors (missing object, S3 timeout). Naming the class
        # keeps this distinguishable from "the video has no frames" — the
        # caller's broad except would flatten both into "fetch failed".
        logger.exception(f"[resource_fetch] frames unavailable for {name!r}: {exc!r}")
        return {
            "error": (
                f"video file not available for frame extraction "
                f"({exc.__class__.__name__})"
            )
        }

    if result.error:
        return {"error": f"frame extraction failed: {result.error}"}

    sampled = list(result.sampled_at_seconds or [])
    blocks: list[dict[str, Any]] = []
    kept_at: list[Optional[float]] = []
    oversize = 0
    for idx, att in enumerate(result.attachments):
        data_url = att.data_url or ""
        if not data_url:
            continue
        if len(data_url) > _FRAMES_MAX_DATA_URL_CHARS:
            oversize += 1
            logger.warning(
                f"[resource_fetch] frame {idx} of {name!r} is "
                f"{len(data_url)} data-URL chars, over the per-frame budget; "
                f"dropping"
            )
            continue
        ts = sampled[idx] if idx < len(sampled) else None
        blocks.append(
            {
                "type": "image_url",
                "url": data_url,
                "mime": att.mime or "image/jpeg",
                # Survives _strip_image_urls (only the url is replaced), so
                # the tool message still tells the model WHEN each frame is
                # from and in what order — the promoted image parts carry
                # the pixels in the same order but no labels.
                "alt": att.alt_text or "",
            }
        )
        kept_at.append(round(ts, 2) if ts is not None else None)

    if not blocks:
        if oversize:
            return {
                "error": (
                    f"every frame extracted from {name!r} exceeded the "
                    f"per-frame size budget; none could be inlined"
                )
            }
        return {"error": f"no usable frames could be extracted from {name!r}"}

    missing = max(0, num_frames - len(blocks))
    out: dict[str, Any] = {
        "content": blocks,
        "meta": {
            "name": name,
            "mode": "frames",
            "frames_requested": num_frames,
            "frames_returned": len(blocks),
            "duration_seconds": result.duration_seconds,
            "sampled_at_seconds": kept_at,
        },
    }
    if missing:
        detail = f" ({oversize} exceeded the per-frame size budget)" if oversize else ""
        out["warning"] = (
            f"{missing} of {num_frames} frames could not be extracted{detail}; "
            f"the frames returned are the ones that succeeded"
        )
    return out


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
      - video:   summary (default) / transcript / frames
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
    #          r.file_path, r.media_id, r.notes AS brief,
    #          r.transcript_status, r.summary_status
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
            # mode='frames' needs the PR-B ladder (resources.file_path is
            # NULL by design for source_type='web' rows), and that ladder's
            # second rung keys off media_id — read it alongside the access
            # check rather than paying a second round trip for it.
            Resources.media_id,
            Resources.notes.label("brief"),
            # Read alongside the access check (one round trip) so the
            # video/audio branch can tell "being generated" from "never
            # processed" — spec 2026-08-17 §1-F1.
            Resources.transcript_status,
            Resources.summary_status,
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
    # ``mode`` is deliberately not consulted here: an agent that asks an
    # image for mode='frames' means "let me see it", and the image IS the
    # frame. Returning the picture is the answer to that question; a typed
    # "wrong mode" error would only cost the model another round trip.
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
    # resource_transcripts, keyed by resource_id (UNIQUE today, so at most
    # one row each). The pre-2026-08 implementation joined `public.videos`, a
    # table renamed away by migration 066 — every video/audio fetch raised and
    # degraded to "fetch failed" for months. Access is already validated by
    # the resources+resource_items team-membership check above; these two
    # tables carry no scope mixin, so no wrapper is needed here. The int()
    # bind is required: resource_id arrives as str and asyncpg's int8 codec
    # rejects str for BIGINT columns.
    #
    # "newest row wins" is stated explicitly rather than leaning on the UNIQUE
    # constraint: `scalar_one_or_none()` would raise MultipleResultsFound the
    # day that constraint is relaxed, and the broad except upstream would turn
    # that into an opaque "fetch failed" — the exact silent-degradation shape
    # this branch just spent months in. NULLS LAST because Postgres sorts
    # NULLs FIRST under DESC, which would let an untimestamped backfill row
    # outrank a real one (created_at defaults to now(), so NULL only arrives
    # that way).
    if mime.startswith("video/") or mime.startswith("audio/"):
        m = mode or ("summary" if mime.startswith("video/") else "transcript")
        rid = int(resource_id)
        # The status columns only ever receive terminal values, so a
        # column-only read could never say "in flight" — the live half comes
        # from task_tracking (see services/ai/resource_ai_status).
        _effective = (await effective_ai_statuses({resource_id: row})).get(
            resource_id, {}
        )
        transcript_status = _effective.get("transcript_status")
        summary_status = _effective.get("summary_status")
        in_flight = {"pending", "processing"}
        if m == "summary":
            async with read_scope() as session:
                text = (
                    await session.execute(
                        select(ResourceSummaries.summary_text)
                        .where(ResourceSummaries.resource_id == rid)
                        .order_by(ResourceSummaries.created_at.desc().nullslast())
                        .limit(1)
                    )
                ).scalar()
            if not text:
                # A summary can only follow a transcript, so a resource that
                # is still transcribing is "in flight" for summary too — and
                # that is the common case, since the frontend chains
                # transcribe → summarize (summary_status is still 'none'
                # while transcription runs). Reporting a flat failure there
                # is exactly the bug this branch is fixing.
                if summary_status in in_flight or transcript_status in in_flight:
                    # The in-flight step may be the *transcript* (frontend
                    # chains transcribe → summarize), so stay neutral about
                    # which stage is running.
                    return {
                        "error": (
                            "summary is not ready yet; the media is still "
                            "being processed — ask the user to retry shortly"
                        )
                    }
                return {"error": "summary not available; resource not yet processed"}
            return {"content": text, "meta": {"name": row["name"], "mode": "summary"}}
        if m == "transcript":
            async with read_scope() as session:
                text = (
                    await session.execute(
                        select(ResourceTranscripts.full_text)
                        .where(ResourceTranscripts.resource_id == rid)
                        .order_by(ResourceTranscripts.created_at.desc().nullslast())
                        .limit(1)
                    )
                ).scalar()
            if not text:
                if transcript_status in in_flight:
                    return {
                        "error": (
                            "transcript is being generated; ask the user to "
                            "retry shortly"
                        )
                    }
                return {"error": "transcript not available; resource not yet processed"}
            return {
                "content": text,
                "meta": {"name": row["name"], "mode": "transcript"},
            }
        if m == "frames":
            if not mime.startswith("video/"):
                return {
                    "error": (
                        "frames are only available for video resources; this "
                        "one is audio — use mode='transcript'"
                    )
                }
            count, count_error = _parse_frame_count(args)
            if count_error or count is None:
                return {"error": count_error or "invalid frames argument"}

            from app.services.library.resource_file_path import (
                resolve_resource_file_path,
            )

            # ⚠️ NOT row["file_path"]. That column is empty BY DESIGN for
            # source_type='web' rows (platform downloads, over half the
            # videos in production) — the shared download fields live on
            # parsed_media (PR-B). Reading the column directly would report
            # "no video file" for exactly the videos the user just
            # downloaded and is asking about. Ladder + sources:
            # services/library/resource_file_path.py's module docstring.
            # The same function also returns None for photo albums, whose
            # download_path is a DIRECTORY prefix — handing that to ffmpeg
            # would trade a clean error for a confusing crash.
            #
            # No scope wrapper: the ladder's second rung reads ParsedMedia,
            # which carries no scope mixin (same reason the summary /
            # transcript reads above need none), and access to this row was
            # already validated by the team-membership check.
            file_path = await resolve_resource_file_path(dict(row))
            if not file_path:
                return {
                    "error": (
                        "video file not available for frame extraction; the "
                        "download may still be in progress, or this resource "
                        "is a photo album rather than a single video file"
                    )
                }
            return await _video_frames(
                file_path=str(file_path),
                num_frames=count,
                name=row["name"],
            )
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
