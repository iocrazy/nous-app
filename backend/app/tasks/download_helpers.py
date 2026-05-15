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


def maybe_chain_transcode(platform_id: str, user_id: str, *, flow_id: str | None = None):
    """Chain HLS transcoding after download if the resource is a video.

    Pre-creates the transcode task_tracking row carrying ``flow_id`` so
    it shows up under the same pipeline chain as download in the UI."""
    try:
        import uuid as _uuid

        from app.repositories.resources_repository import ResourcesRepository
        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager
        from app.workflows.transcode import transcode_workflow

        repo = ResourcesRepository()
        resource = run_async(repo.get_resource_by_platform_id(platform_id))
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
        versions = run_async(repo.get_versions(resource_id))
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

        wf_id = str(_uuid.uuid4())
        try:
            run_async(
                get_task_manager().create(
                    user_id=user_id,
                    task_type="transcode",
                    title="Transcode",
                    media_id=str(platform_id),
                    resource_id=resource_id,
                    dbos_workflow_id=wf_id,
                    flow_id=flow_id,
                )
            )
        except Exception as e:
            logger.warning(f"[Transcode/Chain] pre-create task_tracking row: {e}")

        run_async(
            start_workflow_routed(
                "transcode",
                dbos_workflow_callable=transcode_workflow,
                dbos_workflow_kwargs={
                    "resource_id": resource_id,
                    "version_id": version_id,
                    "user_id": user_id,
                },
                workflow_id=wf_id,
            )
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


def chain_transcript_summary_for_tags(
    platform_id: str,
    user_id: str,
    *,
    flow_id: str | None = None,
):
    """Dispatch ai_transcription / ai_summary for a resource IFF it
    carries the matching intent tags. Called by ``extract_audio_workflow``
    once the audio asset is on disk — so no audio-ready gate is needed
    here (the caller guarantees it).

      tag "Transcript" → ai_transcription_workflow
      tag "Summary"    → ai_summary_workflow (implies Transcript: the
                         summary step needs transcript text)

    Each dispatched workflow pre-creates its task_tracking row with
    ``flow_id`` so it shows up under the same pipeline chain."""
    try:
        import uuid as _uuid

        from app.repositories.media_repository import MediaRepository
        from app.repositories.resources_repository import ResourcesRepository
        from app.services.infra.dbos_orchestrator import start_workflow_routed
        from app.services.infra.unified_task_manager import get_task_manager

        media = run_async(MediaRepository().get_by_platform_id(platform_id))
        parsed_media_id = (media or {}).get("id")
        if not parsed_media_id:
            logger.warning(f"[AI] No parsed_media row for {platform_id}, skip")
            return

        res_repo = ResourcesRepository()
        resource = run_async(
            res_repo.get_resource_by_media_id_and_creator(str(parsed_media_id), user_id)
        )
        if not resource:
            logger.debug(
                f"[AI] No resource for media={parsed_media_id} user={user_id}, skip"
            )
            return
        resource_id = str(resource["id"])

        tag_names = run_async(read_resource_tag_names(resource_id))
        want_transcript = "Transcript" in tag_names or "Summary" in tag_names
        want_summary = "Summary" in tag_names

        if not want_transcript:
            logger.debug(
                f"[AI] No Transcript/Summary tag on resource={resource_id}, skip"
            )
            return

        mgr = get_task_manager()

        from app.workflows.ai_transcription import ai_transcription_workflow

        tr_wf_id = str(_uuid.uuid4())
        try:
            run_async(
                mgr.create(
                    user_id=user_id,
                    task_type="ai_transcription",
                    title="Transcript",
                    media_id=str(platform_id),
                    resource_id=resource_id,
                    dbos_workflow_id=tr_wf_id,
                    flow_id=flow_id,
                )
            )
        except Exception as e:
            logger.warning(f"[AI] pre-create ai_transcription row: {e}")
        run_async(
            start_workflow_routed(
                "ai_transcription",
                dbos_workflow_callable=ai_transcription_workflow,
                dbos_workflow_kwargs={
                    "parsed_media_id": int(parsed_media_id),
                    "user_id": user_id,
                },
                workflow_id=tr_wf_id,
            )
        )

        if want_summary:
            from app.workflows.ai_summary import ai_summary_workflow

            sm_wf_id = str(_uuid.uuid4())
            try:
                run_async(
                    mgr.create(
                        user_id=user_id,
                        task_type="ai_summary",
                        title="Summary",
                        media_id=str(platform_id),
                        resource_id=resource_id,
                        dbos_workflow_id=sm_wf_id,
                        flow_id=flow_id,
                    )
                )
            except Exception as e:
                logger.warning(f"[AI] pre-create ai_summary row: {e}")
            run_async(
                start_workflow_routed(
                    "ai_summary",
                    dbos_workflow_callable=ai_summary_workflow,
                    dbos_workflow_kwargs={
                        "parsed_media_id": int(parsed_media_id),
                        "user_id": user_id,
                    },
                    workflow_id=sm_wf_id,
                )
            )

        logger.info(
            f"[AI] transcript/summary chained for {platform_id} "
            f"(summary={want_summary}, resource={resource_id})"
        )
    except Exception as e:
        logger.warning(
            f"[AI] chain_transcript_summary_for_tags failed for {platform_id}: "
            f"{type(e).__name__}: {e!r}"
        )


def maybe_chain_ai_pipeline(platform_id: str, user_id: str, *, flow_id: str | None = None):
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

        media = run_async(MediaRepository().get_by_platform_id(platform_id))
        parsed_media_id = (media or {}).get("id")
        if not parsed_media_id:
            logger.warning(f"[AI] No parsed_media row for {platform_id}, skip analyze")
            return

        res_repo = ResourcesRepository()
        resource = run_async(
            res_repo.get_resource_by_media_id_and_creator(str(parsed_media_id), user_id)
        )
        if not resource:
            logger.debug(
                f"[AI] No resource for media={parsed_media_id} user={user_id}, "
                "skip analyze"
            )
            return
        resource_id = str(resource["id"])

        tag_names = run_async(read_resource_tag_names(resource_id))
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

        wf_id = str(_uuid.uuid4())
        try:
            run_async(
                get_task_manager().create(
                    user_id=user_id,
                    task_type="ai_extract",
                    title="Analyze",
                    media_id=str(platform_id),
                    resource_id=resource_id,
                    dbos_workflow_id=wf_id,
                    flow_id=flow_id,
                )
            )
        except Exception as e:
            logger.warning(f"[AI] pre-create analyze task_tracking row: {e}")
        run_async(
            start_workflow_routed(
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
        )
        logger.info(f"[AI] analyze chained after download: {platform_id}")

    except Exception as e:
        logger.warning(
            f"[AI] Failed to chain analyze for {platform_id}: "
            f"{type(e).__name__}: {e!r}"
        )


# ─── URL availability helpers ─────────────────────────────────────────


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

    try:
        from app.services.media.parsers.douyin_parse.formatter import DouyinFormatter
        from app.services.media.parsers.douyin_parse.ies_parser import IesDouyinParser
        from app.services.media.parsers.douyin_parse.ua_pool import pick_ua

        # One UA for the whole re-parse sequence (LightHTTP → BrowserAuto).
        reparse_ua = pick_ua()

        # --- Attempt 1: IesDouyinParser (fast HTTP, no browser) ---
        aweme_detail = run_async(
            IesDouyinParser.parse(original_url, user_agent=reparse_ua)
        )
        parse_method = "LightHTTP"

        # If short URL failed, try directly with platform_id (bypass URL redirect)
        if not aweme_detail and platform_id:
            logger.info(
                f"[Download/URL] Short URL failed, trying platform_id directly: {platform_id}"
            )
            aweme_detail = run_async(
                IesDouyinParser._fetch_share_page(platform_id, user_agent=reparse_ua)
            )
            if aweme_detail:
                IesDouyinParser._process_video_urls(aweme_detail)
                parse_method = "LightHTTP-directID"

        if aweme_detail:
            new_parsed = run_async(
                DouyinFormatter.parse_aweme_detail(
                    aweme_detail=aweme_detail,
                    valid_url=original_url,
                    download_video=True,
                    download_music=True,
                    download_cover=True,
                )
            )
        else:
            new_parsed = None

        # Check which types are still missing after LightHTTP
        still_missing = []
        if new_parsed:
            for t in missing_types:
                field = url_fields.get(t)
                if field and not new_parsed.get(field):
                    still_missing.append(t)
        else:
            still_missing = list(missing_types)

        # --- Attempt 2: DrissionPageParser browser fallback (if still missing) ---
        if still_missing:
            logger.info(
                f"[Download/URL] LightHTTP still missing {still_missing}, "
                f"falling back to BrowserAuto for {platform_id}"
            )
            try:
                from app.services.media.parsers.douyin_parse.drissionpage_parser import (
                    DrissionPageParser,
                )

                browser_detail = run_async(
                    DrissionPageParser.fetch_one_video(
                        original_url, user_agent=reparse_ua
                    )
                )
                if browser_detail:
                    parse_method = "BrowserAuto"
                    browser_parsed = run_async(
                        DouyinFormatter.parse_aweme_detail(
                            aweme_detail=browser_detail,
                            valid_url=original_url,
                            download_video=True,
                            download_music=True,
                            download_cover=True,
                        )
                    )
                    if browser_parsed:
                        # Merge browser results into new_parsed (browser data wins)
                        if new_parsed:
                            for t in still_missing:
                                field = url_fields.get(t)
                                if field and browser_parsed.get(field):
                                    new_parsed[field] = browser_parsed[field]
                        else:
                            new_parsed = browser_parsed
                        logger.info(
                            f"[Download/URL] BrowserAuto re-parse succeeded for {platform_id}"
                        )
                    else:
                        logger.warning(
                            f"[Download/URL] BrowserAuto parse yielded no data for {platform_id}"
                        )
                else:
                    logger.warning(
                        f"[Download/URL] BrowserAuto returned empty for {platform_id}"
                    )
            except Exception as e:
                logger.warning(
                    f"[Download/URL] BrowserAuto fallback failed for {platform_id}: {e}"
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
    try:
        async with safe_async_client(http2=True) as client:
            resp = await client.head(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return True, "ok"
            reason = f"HTTP {resp.status_code}"
            logger.debug(f"[Download/Validate] HEAD {reason} for {url[:80]}...")
            return False, reason
    except Exception as e:
        reason = str(e)[:100]
        logger.debug(f"[Download/Validate] HEAD failed for {url[:80]}...: {reason}")
        return False, reason


def validate_and_refresh_urls(
    platform_id: str, media: dict, url_field: str, type_key: str
) -> tuple[dict, bool, str]:
    """Validate URLs via HEAD, refresh if expired, return (media, urls_valid, reason).

    Flow:
    1. Check current URLs accessibility via HEAD
    2. If all inaccessible → clear URLs in DB → re-parse → check new URLs
    3. Return whether URLs are valid for download
    """
    from app.repositories.media_repository import MediaRepository as _MR_val

    urls = media.get(url_field) or []
    if not urls:
        return media, False, "no URLs available"

    # Test current URLs (handle nested lists: [[url1, url2], [url3, url4]])
    fail_reason = ""
    for item in urls:
        # Nested list: item is [url1, url2, ...] — test first URL
        url = item[0] if isinstance(item, list) and item else item
        if not isinstance(url, str):
            continue
        ok, reason = run_async(check_url_accessible(url))
        if ok:
            return media, True, "ok"
        fail_reason = reason

    # All URLs inaccessible → clear and re-parse
    logger.info(
        f"[Download/Validate] All {len(urls)} {type_key} URLs inaccessible for {platform_id} "
        f"({fail_reason}), clearing and re-parsing..."
    )
    media[url_field] = []
    run_async(_MR_val().update(platform_id, {url_field: None}))
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
