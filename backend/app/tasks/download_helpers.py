# app/tasks/download_helpers.py

"""
Download Helper Functions

URL validation/refresh, audio extraction from video, and post-download
pipeline chaining (transcode, AI).
"""

import os

from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.core.utils import Utils
from app.tasks.utils import run_async

# ─── Post-download chain helpers ─────────────────────────────────────


async def maybe_chain_transcode(
    platform_id: str,
    user_id: str,
    *,
    flow_id: str | None = None,
    video_title: str = "",
):
    """Chain HLS transcoding after download if the resource is a video.

    Pre-creates the transcode task_tracking row carrying ``flow_id`` so
    it shows up under the same pipeline chain as download in the UI.

    §2.4b: async-native — awaited from the async ``chain_followups_step``;
    the resource reads + task_manager.create + dispatch run on the caller's
    loop (no run_async fresh-loop bridge, ORM-safe). The ambient USER scope
    set by the caller's ``async with request_scope`` is now naturally visible
    to the awaited reads (same context, no copy_context thread hop)."""
    try:
        import uuid as _uuid

        from app.repositories.resources_repository import ResourcesRepository
        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager
        from app.workflows.transcode import transcode_workflow

        repo = ResourcesRepository()
        resource = await repo.get_resource_by_platform_id(platform_id)
        if not resource:
            logger.info(
                f"[Transcode/Chain] No resource found for platform_id={platform_id}"
            )
            return

        mime = resource.get("mime_type") or ""
        if not mime.startswith("video/"):
            logger.debug(f"[Transcode/Chain] Not a video ({mime}), skip: {platform_id}")
            return

        resource_id = str(resource["id"])
        versions = await repo.get_versions(resource_id)
        if not versions:
            logger.info(
                f"[Transcode/Chain] No versions for resource {resource_id}, skip (download)"
            )
            return

        latest = versions[0]
        version_id = str(latest["id"])
        logger.info(
            f"[Transcode/Chain] Chaining transcode: resource={resource_id}, "
            f"version={version_id}, mime={mime}, platform_id={platform_id}"
        )

        title_clip = (video_title or resource.get("filename") or platform_id or "")[:50]
        wf_id = str(_uuid.uuid4())
        try:
            await get_task_manager().create(
                user_id=user_id,
                task_type="transcode",
                title=f"Transcode {title_clip}",
                media_id=str(platform_id),
                resource_id=resource_id,
                dbos_workflow_id=wf_id,
                flow_id=flow_id,
            )
        except Exception as e:
            logger.warning(f"[Transcode/Chain] pre-create task_tracking row: {e}")

        await start_workflow_routed(
            "transcode",
            dbos_workflow_callable=transcode_workflow,
            dbos_workflow_kwargs={
                "resource_id": resource_id,
                "version_id": version_id,
                "user_id": user_id,
            },
            workflow_id=wf_id,
        )
    except Exception as e:
        logger.error(f"[Transcode/Chain] Failed for {platform_id}: {e}", exc_info=True)


async def read_resource_tag_names(resource_id: str) -> set[str]:
    """Tag names attached to a resource. Uses the supabase admin client
    so RLS doesn't get in the way of chain helpers / workflow steps."""
    from app.db.supabase_client import get_async_supabase_admin

    client = await get_async_supabase_admin()
    r = await (
        client.table("resource_tags")
        .select("tags(name)")
        .eq("resource_id", resource_id)
        .execute()
    )
    names: set[str] = set()
    for row in r.data or []:
        t = row.get("tags") or {}
        n = t.get("name")
        if n:
            names.add(n)
    return names


async def chain_transcript_summary_for_tags(
    platform_id: str,
    user_id: str,
    *,
    flow_id: str | None = None,
    video_title: str = "",
):
    """Dispatch ai_transcription for a resource IFF it carries Transcript
    or Summary tags. Called by ``extract_audio_workflow`` once the audio
    asset is on disk — so no audio-ready gate is needed here (the caller
    guarantees it).

      tag "Transcript" → ai_transcription_workflow
      tag "Summary"    → handled by ai_transcription_workflow's success
                         hook (it chains ai_summary_workflow when the
                         resource has Summary tag). Dispatching summary
                         concurrently with transcript caused "no
                         transcript yet" failures (QA 2026-05-17 #24);
                         summary is now strictly downstream of transcript.

    Each dispatched workflow pre-creates its task_tracking row with
    ``flow_id`` so it shows up under the same pipeline chain."""
    try:
        import uuid as _uuid

        from app.repositories.media_repository import MediaRepository
        from app.repositories.resources_repository import ResourcesRepository
        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager

        media = await MediaRepository().get_by_platform_id(platform_id)
        parsed_media_id = (media or {}).get("id")
        if not parsed_media_id:
            logger.warning(f"[AI] No parsed_media row for {platform_id}, skip")
            return

        res_repo = ResourcesRepository()
        resource = await res_repo.get_resource_by_media_id_and_creator(
            str(parsed_media_id), user_id
        )

        if not resource:
            logger.debug(
                f"[AI] No resource for media={parsed_media_id} user={user_id}, skip"
            )
            return
        resource_id = str(resource["id"])

        tag_names = await read_resource_tag_names(resource_id)
        want_transcript = "Transcript" in tag_names or "Summary" in tag_names

        if not want_transcript:
            logger.debug(
                f"[AI] No Transcript/Summary tag on resource={resource_id}, skip"
            )
            return

        # Pre-check user_settings: ai_transcription.load_transcribe_inputs
        # raises "no user_settings for {user_id}" when the row is absent
        # (every first-time user before they configure AI). With chain
        # auto-dispatch (post-PR-283), this guarantees a red error per
        # download for every new user. Detect upfront and skip both
        # transcript and summary dispatch with an INFO log.
        # QA evidence: tasks #24 (2026-05-17).
        from app.repositories.user_settings_repository import UserSettingsRepository

        try:
            settings_row = await UserSettingsRepository().get_by_user_id(user_id)
        except Exception as e:
            logger.warning(
                f"[AI] user_settings probe failed for {user_id} ({e!r}); "
                "skipping transcript/summary chain to avoid red error"
            )
            return
        if not settings_row:
            logger.info(
                f"[AI] No user_settings for {user_id} — skipping "
                f"transcript/summary chain (user has not configured AI). "
                f"Tags {tag_names} on resource={resource_id} would otherwise "
                "have triggered ai_transcription/ai_summary."
            )
            return

        mgr = get_task_manager()
        title_clip = (video_title or (media or {}).get("title") or platform_id or "")[
            :50
        ]

        from app.workflows.ai_transcription import ai_transcription_workflow

        tr_wf_id = str(_uuid.uuid4())
        try:
            await mgr.create(
                user_id=user_id,
                task_type="ai_transcription",
                title=f"Transcript {title_clip}",
                media_id=str(platform_id),
                resource_id=resource_id,
                dbos_workflow_id=tr_wf_id,
                flow_id=flow_id,
            )

        except Exception as e:
            logger.warning(f"[AI] pre-create ai_transcription row: {e}")
        await start_workflow_routed(
            "ai_transcription",
            dbos_workflow_callable=ai_transcription_workflow,
            dbos_workflow_kwargs={
                "parsed_media_id": int(parsed_media_id),
                "user_id": user_id,
            },
            workflow_id=tr_wf_id,
        )

        # Summary is dispatched by ai_transcription_workflow's success
        # hook (chain_summary_for_tags), not here. See docstring for why.
        logger.info(
            f"[AI] transcript chained for {platform_id} (resource={resource_id})"
        )
    except Exception as e:
        logger.warning(
            f"[AI] chain_transcript_summary_for_tags failed for {platform_id}: "
            f"{type(e).__name__}: {e!r}"
        )


async def chain_summary_for_tags(parsed_media_id: int, user_id: str):
    """Dispatch ai_summary_workflow IFF the resource tied to
    ``parsed_media_id`` carries the Summary tag. Called by
    ``ai_transcription_workflow``'s success path so summary always runs
    after transcript completes (vs the pre-this-fix concurrent dispatch
    that hit "no transcript yet" 100% of the time — QA 2026-05-17 #24).

    Best-effort. Failures are logged and swallowed — transcript success
    is what counts.
    """
    try:
        import uuid as _uuid

        from app.repositories.media_repository import MediaRepository
        from app.repositories.resources_repository import ResourcesRepository
        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager
        from app.workflows.ai_summary import ai_summary_workflow

        media = await MediaRepository().get_by_id(int(parsed_media_id))
        if not media:
            logger.debug(f"[AI] summary chain: no parsed_media {parsed_media_id}")
            return
        platform_id = media.get("platform_id")

        res_repo = ResourcesRepository()
        resource = await res_repo.get_resource_by_media_id_and_creator(
            str(parsed_media_id), user_id
        )

        if not resource:
            return
        resource_id = str(resource["id"])

        tag_names = await read_resource_tag_names(resource_id)
        if "Summary" not in tag_names:
            return

        # Inherit flow_id from the transcript task on the same chain so
        # the summary card lands in the same FlowGroupCard as parse →
        # download → extract_audio → transcript.
        from app.db.supabase_client import get_async_supabase_admin

        async def _read_flow_id() -> str | None:
            client = await get_async_supabase_admin()
            r = await (
                client.table("task_tracking")
                .select("flow_id")
                .eq("media_id", str(platform_id) if platform_id else "")
                .eq("task_type", "ai_transcription")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = r.data or []
            return rows[0].get("flow_id") if rows else None

        flow_id = await _read_flow_id()

        title_clip = (media.get("title") or platform_id or str(parsed_media_id))[:50]
        sm_wf_id = str(_uuid.uuid4())
        try:
            await get_task_manager().create(
                user_id=user_id,
                task_type="ai_summary",
                title=f"Summary {title_clip}",
                media_id=str(platform_id) if platform_id else None,
                resource_id=resource_id,
                dbos_workflow_id=sm_wf_id,
                flow_id=flow_id,
            )

        except Exception as e:
            logger.warning(f"[AI] pre-create ai_summary row: {e}")
        await start_workflow_routed(
            "ai_summary",
            dbos_workflow_callable=ai_summary_workflow,
            dbos_workflow_kwargs={
                "parsed_media_id": int(parsed_media_id),
                "user_id": user_id,
            },
            workflow_id=sm_wf_id,
        )

        logger.info(
            f"[AI] summary chained post-transcript for parsed_media_id={parsed_media_id}"
        )
    except Exception as e:
        logger.warning(
            f"[AI] chain_summary_for_tags failed for "
            f"parsed_media_id={parsed_media_id}: {type(e).__name__}: {e!r}"
        )


async def maybe_chain_ai_pipeline(
    platform_id: str,
    user_id: str,
    *,
    flow_id: str | None = None,
    video_title: str = "",
):
    """Chain the cover-analysis workflow after download IFF the resource
    carries the "Analyze" intent tag.

    Transcript/Summary are NOT dispatched here anymore — they depend on
    the extracted audio asset, so ``extract_audio_workflow`` dispatches
    them via ``chain_transcript_summary_for_tags`` once the audio is on
    disk. Analyze only needs the cover, so it stays in the download
    chain (decoupled from audio extraction success)."""
    try:
        import uuid as _uuid

        from app.repositories.media_repository import MediaRepository
        from app.repositories.resources_repository import ResourcesRepository
        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager

        media = await MediaRepository().get_by_platform_id(platform_id)
        parsed_media_id = (media or {}).get("id")
        if not parsed_media_id:
            logger.warning(f"[AI] No parsed_media row for {platform_id}, skip analyze")
            return

        res_repo = ResourcesRepository()
        resource = await res_repo.get_resource_by_media_id_and_creator(
            str(parsed_media_id), user_id
        )

        if not resource:
            logger.debug(
                f"[AI] No resource for media={parsed_media_id} user={user_id}, "
                "skip analyze"
            )
            return
        resource_id = str(resource["id"])

        tag_names = await read_resource_tag_names(resource_id)
        if "Analyze" not in tag_names:
            logger.debug(
                f"[AI] No Analyze tag on resource={resource_id}, skip analyze chain"
            )
            return

        cover_urls = (media or {}).get("cover_urls") or []
        cover_url = cover_urls[0] if cover_urls else None
        if not cover_url:
            logger.info(f"[AI] No cover_url for {platform_id}, skip analyze")
            return

        from app.workflows.analyze_l1 import analyze_l1_workflow

        title_clip = (video_title or (media or {}).get("title") or platform_id or "")[
            :50
        ]
        wf_id = str(_uuid.uuid4())
        try:
            await get_task_manager().create(
                user_id=user_id,
                task_type="ai_extract",
                title=f"Analyze {title_clip}",
                media_id=str(platform_id),
                resource_id=resource_id,
                dbos_workflow_id=wf_id,
                flow_id=flow_id,
            )

        except Exception as e:
            logger.warning(f"[AI] pre-create analyze task_tracking row: {e}")
        await start_workflow_routed(
            "ai_extract",
            dbos_workflow_callable=analyze_l1_workflow,
            dbos_workflow_kwargs={
                "media_id": parsed_media_id,
                "cover_url": cover_url,
                "title": (media or {}).get("title") or "",
                "description": (media or {}).get("description") or "",
                "user_id": user_id,
            },
            workflow_id=wf_id,
        )

        logger.info(f"[AI] analyze chained after download: {platform_id}")

    except Exception as e:
        logger.warning(
            f"[AI] Failed to chain analyze for {platform_id}: "
            f"{type(e).__name__}: {e!r}"
        )


# ─── URL availability helpers ─────────────────────────────────────────


def reparse_douyin_via_chain(
    platform_id: str,
    original_url: str | None,
    *,
    user_id: str | None = None,
    user_agent: str | None = None,
) -> tuple[dict | None, str]:
    """Sync bridge over the unified douyin re-parse (ABogus → DrissionPage,
    original_url first then bare aweme_id). Returns (parsed_data, method);
    (None, "") when every attempt fails. Never raises."""
    from app.services.media.parsers.douyin_parse.parse_chain import reparse_douyin
    from app.services.media.parsers.douyin_parse.ua_pool import pick_ua

    try:
        result = run_async(
            reparse_douyin(
                platform_id,
                original_url=original_url,
                user_id=user_id,
                user_agent=user_agent or pick_ua(),
                download_video=True,
                download_music=True,
                download_cover=True,
            )
        )
    except Exception as e:
        logger.warning(
            f"[Download/URL] unified-chain re-parse raised for {platform_id}: {e}"
        )
        return None, ""
    if not result:
        return None, ""
    return result


def ensure_download_urls(
    platform_id: str, media: dict, needed_types: list[str]
) -> dict:
    """Check if download URLs are available for needed types. Re-parse if missing.

    Returns the media dict with refreshed URLs if re-parsed.
    """
    url_fields = {
        "video": "video_download_urls",
        "cover": "cover_urls",
        "image": "image_download_urls",
    }

    missing_types = []
    for t in needed_types:
        field = url_fields.get(t)
        val = media.get(field)
        has_urls = bool(val) and (
            isinstance(val, list) and len(val) > 0 if isinstance(val, list) else True
        )
        logger.info(
            f"[Download/URL] Check {t}: field={field}, has_urls={has_urls}, type={type(val).__name__}"
        )
        if field and not has_urls:
            missing_types.append(t)

    if not missing_types:
        logger.info(f"[Download/URL] All needed URLs available for {platform_id}")
        return media

    logger.info(
        f"[Download/URL] Missing URLs for {missing_types} on {platform_id}, "
        f"re-parsing original_url..."
    )

    original_url = media.get("original_url")
    if not original_url:
        logger.warning(
            f"[Download/URL] No original_url for {platform_id}, cannot re-parse"
        )
        return media

    # The douyin chain (ABogus / DrissionPage) is douyin-specific. For
    # yt-dlp platforms (bilibili / youtube / twitter / xhs / ...), calling
    # it just wastes HTTP calls while the outer caller's yt-dlp fallback
    # (download_strategies.py video failure branch) is the
    # actually-correct recovery path.
    source_platform = media.get("source_platform")
    if source_platform not in ("douyin", "tiktok"):
        logger.info(
            f"[Download/URL] Skip douyin re-parse for {source_platform} "
            f"platform_id={platform_id} — yt-dlp fallback owns recovery"
        )
        return media

    try:
        # Unified chain (ABogus → DrissionPage) — the SAME chain the
        # initial parse uses, so re-parse can't silently rot again the
        # way the old LightHTTP→BrowserAuto fork did (LightHTTP was
        # permanently anti-bot blocked → every re-parse failed → yt-dlp
        # HEVC fallback → black-screen downloads).
        new_parsed, parse_method = reparse_douyin_via_chain(
            platform_id,
            original_url,
            user_id=media.get("user_id"),
        )

        if not new_parsed:
            logger.warning(
                f"[Download/URL] All re-parse attempts failed for {platform_id}"
            )
            return media

        # Update DB with refreshed URLs
        from app.repositories.media_repository import MediaRepository as _MR

        update_fields = {}
        for t in missing_types:
            field = url_fields.get(t)
            if field and new_parsed.get(field):
                update_fields[field] = new_parsed[field]
                media[field] = new_parsed[field]
                logger.info(
                    f"[Download/URL] Refreshed {field} for {platform_id} "
                    f"({len(new_parsed[field])} URLs, via {parse_method})"
                )

        # Also update music_play_urls if re-parsed (carousel needs standalone music)
        if new_parsed.get("music_play_urls"):
            update_fields["music_play_urls"] = new_parsed["music_play_urls"]
            media["music_play_urls"] = new_parsed["music_play_urls"]
            logger.info(
                f"[Download/URL] Refreshed music_play_urls for {platform_id} "
                f"({len(new_parsed['music_play_urls'])} URLs, via {parse_method})"
            )

        if update_fields:
            run_async(_MR().update(platform_id, update_fields))
        else:
            logger.warning(
                f"[Download/URL] Re-parse found no new URLs for {missing_types}"
            )

    except Exception as e:
        logger.error(f"[Download/URL] Re-parse failed for {platform_id}: {e}")

    return media


# ─── URL validation helpers ───────────────────────────────────────────


async def check_url_accessible(url: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Quick HEAD request to verify a download URL is reachable (not expired/blocked).

    Returns (accessible, reason) tuple.
    """
    from app.boundary import safe_async_client

    headers = Utils.get_headers()
    # Per-URL outcomes log at INFO/WARNING (not DEBUG): prod doesn't
    # persist DEBUG, which left a 30s+ diagnostic black hole around the
    # 2026-06-10 "URLs cleared on a DNS blip" incident.
    try:
        async with safe_async_client(http2=True) as client:
            resp = await client.head(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return True, "ok"
            reason = f"HTTP {resp.status_code}"
            logger.info(f"[Download/Validate] HEAD {reason} for {url[:80]}...")
            return False, reason
    except Exception as e:
        reason = str(e)[:100] or type(e).__name__
        logger.warning(f"[Download/Validate] HEAD failed for {url[:80]}...: {reason}")
        return False, reason


# Definitive HTTP rejections that mean the CDN URL itself is expired or
# blocked. Everything else (DNS/timeout/connect errors, 5xx, 429) is a
# transient condition that says nothing about URL validity.
_PERMANENT_HTTP_CODES = {400, 401, 403, 404, 410}


def is_permanent_url_failure(reason: str) -> bool:
    """Classify a ``check_url_accessible`` failure reason.

    Only definitive HTTP client rejections count as permanent. Clearing
    stored URLs on transient failures (e.g. a 30s aweme.snssdk.com DNS
    blip) destroyed perfectly good state and cascaded into the yt-dlp
    HEVC black-screen fallback — 2026-06-10 P1."""
    if not reason.startswith("HTTP "):
        return False
    try:
        code = int(reason.split()[1])
    except (IndexError, ValueError):
        return False
    return code in _PERMANENT_HTTP_CODES


def validate_and_refresh_urls(
    platform_id: str, media: dict, url_field: str, type_key: str
) -> tuple[dict, bool, str]:
    """Validate URLs via HEAD, refresh if expired, return (media, urls_valid, reason).

    Flow:
    1. Check current URLs accessibility via HEAD
    2. If all fail TRANSIENTLY (DNS/timeout/5xx/429) → soft-fail: keep the
       stored URLs, no re-parse — the caller attempts the GET download
       anyway and the real download failure path owns recovery
    3. If all fail PERMANENTLY (403/404/410 = expired CDN URL) → re-parse
       for fresh URLs. The DB column is never NULLed up front: a failed
       re-parse must leave the previous URLs intact (clearing them on a
       blip was the 2026-06-10 black-screen root cause)
    """
    urls = media.get(url_field) or []
    if not urls:
        return media, False, "no URLs available"

    # Test current URLs (handle nested lists: [[url1, url2], [url3, url4]])
    fail_reason = ""
    saw_permanent = False
    for item in urls:
        # Nested list: item is [url1, url2, ...] — test first URL
        url = item[0] if isinstance(item, list) and item else item
        if not isinstance(url, str):
            continue
        ok, reason = run_async(check_url_accessible(url))
        if ok:
            return media, True, "ok"
        fail_reason = reason
        if is_permanent_url_failure(reason):
            saw_permanent = True

    if not saw_permanent:
        logger.warning(
            f"[Download/Validate] All {len(urls)} {type_key} URLs failed "
            f"TRANSIENTLY for {platform_id} ({fail_reason}) — keeping stored "
            f"URLs, attempting download anyway"
        )
        return media, False, f"transient: {fail_reason}"

    # Permanent rejection → URLs are expired/blocked; re-parse for fresh
    # ones. Only the in-memory copy is cleared (to make ensure_download_urls
    # treat the type as missing) — the DB keeps the old URLs until the
    # re-parse SUCCEEDS and overwrites them.
    logger.info(
        f"[Download/Validate] All {len(urls)} {type_key} URLs permanently "
        f"rejected for {platform_id} ({fail_reason}), re-parsing..."
    )
    media[url_field] = []
    media = ensure_download_urls(platform_id, media, [type_key])

    # Test fresh URLs
    fresh_urls = media.get(url_field) or []
    if not fresh_urls:
        return media, False, "re-parse returned no URLs"

    for item in fresh_urls:
        url = item[0] if isinstance(item, list) and item else item
        if not isinstance(url, str):
            continue
        ok, reason = run_async(check_url_accessible(url))
        if ok:
            logger.info(
                f"[Download/Validate] Fresh {type_key} URLs accessible for {platform_id}"
            )
            return media, True, "ok"
        fail_reason = reason

    logger.warning(
        f"[Download/Validate] Fresh {type_key} URLs also inaccessible for {platform_id}: {fail_reason}"
    )
    return media, False, fail_reason


# ─── Audio extraction helper ─────────────────────────────────────────


def extract_audio_from_video(platform_id: str) -> bool:
    """Extract audio from downloaded video using ffmpeg stream copy (zero-transcode).

    Looks up the video file path from DB, extracts audio to audio.m4a
    in the same directory, and updates music_download_path in DB.

    This is ~100x faster than downloading music separately via URL
    because it's a pure I/O operation with no network or re-encoding.

    Returns True on success, False on failure.
    """
    import subprocess

    from app.repositories.media_repository import MediaRepository as _MR_extract

    repo = _MR_extract()
    media = run_async(repo.get_by_platform_id(platform_id))
    if not media:
        logger.warning(f"[Audio/Extract] No media record for {platform_id}")
        return False

    video_rel_path = media.get("download_path")
    if not video_rel_path:
        logger.warning(f"[Audio/Extract] No download_path for {platform_id}")
        return False

    base_path = Utils.get_download_base_path()
    video_full_path = os.path.join(base_path, video_rel_path)

    if not os.path.exists(video_full_path):
        logger.warning(f"[Audio/Extract] Video file not found: {video_full_path}")
        return False

    output_dir = os.path.dirname(video_full_path)
    audio_full_path = os.path.join(output_dir, "audio.m4a")

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                video_full_path,
                "-vn",  # No video
                "-c:a",
                "copy",  # Copy audio codec (no re-encoding)
                audio_full_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,  # Should be < 1s for stream copy
            **safe_popen_kwargs(),
        )

        if result.returncode != 0:
            logger.warning(
                f"[Audio/Extract] ffmpeg failed (rc={result.returncode}): "
                f"{result.stderr[:300]}"
            )
            return False

        if not os.path.exists(audio_full_path) or os.path.getsize(audio_full_path) == 0:
            logger.warning(
                f"[Audio/Extract] Output file missing or empty: {audio_full_path}"
            )
            if os.path.exists(audio_full_path):
                os.remove(audio_full_path)
            return False

        file_size = os.path.getsize(audio_full_path)
        logger.info(
            f"[Audio/Extract] Success for {platform_id}: "
            f"{Utils.format_file_size(file_size)}"
        )

        # Calculate relative path for DB
        audio_rel_path = os.path.relpath(audio_full_path, base_path)

        # Update DB — extract_audio_path for AI transcription (distinct from music_download_path)
        run_async(
            repo.update(
                platform_id,
                {
                    "extract_audio_path": audio_rel_path,
                },
            )
        )

        return True

    except subprocess.TimeoutExpired:
        logger.warning(f"[Audio/Extract] ffmpeg timed out for {platform_id}")
        return False
    except FileNotFoundError:
        logger.warning("[Audio/Extract] ffmpeg not found in PATH")
        return False
    except Exception as e:
        logger.warning(f"[Audio/Extract] Error for {platform_id}: {e}")
        return False
