# backend/app/api/media_fetch_helpers.py

"""
Media Fetch Helpers

Shared helper functions and models used by media fetch routes.
"""

from typing import Optional

from fastapi import BackgroundTasks, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, field_validator

from app.boundary import ValidatedURL
from app.core.deps import AuthDep
from app.repositories.tags_repository import TagsRepository
from app.repositories.user_logs_repository import log_user_action
from app.repositories.user_settings_repository import UserSettingsRepository
from app.services.media.parsers.douyin_parse.drissionpage_parser import DrissionPageParser
from app.services.media.parsers.douyin_parse.formatter import DouyinFormatter
from app.services.media.parsers.douyin_parse.ies_parser import IesDouyinParser
from app.services.media.parsers.ytdlp_service import YtdlpService

# ============================================
# Models
# ============================================


def _coerce_str_to_list(v):
    """Accept both 'a,b,c' and ['a','b','c']."""
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return v


class MediaFetchRequest(BaseModel):
    """Video fetch request"""

    url: str
    video_bool: bool = True
    cover_bool: bool = True
    use_celery: bool = False
    tags: Optional[list[str]] = None
    tag_ids: Optional[list[str]] = None

    @field_validator("tags", "tag_ids", mode="before")
    @classmethod
    def accept_comma_string(cls, v):
        return _coerce_str_to_list(v) if v is not None else v


class BatchFetchRequest(BaseModel):
    """Batch fetch request"""

    urls: list[str]
    video_bool: bool = True
    cover_bool: bool = True
    use_celery: bool = False
    tags: Optional[list[str]] = None
    tag_ids: Optional[list[str]] = None

    @field_validator("tags", "tag_ids", mode="before")
    @classmethod
    def accept_comma_string(cls, v):
        return _coerce_str_to_list(v) if v is not None else v


# ============================================
# Team resolution
# ============================================


async def resolve_team_id(user_id: str, request: Request) -> Optional[str]:
    """Resolve team_id from X-Team-Id header, falling back to personal team."""
    from app.db.supabase_client import get_async_supabase_admin as _get_admin

    team_id = request.headers.get("X-Team-Id")
    if team_id:
        logger.debug(f"[_resolve_team_id] Using X-Team-Id header: {team_id}")
        return team_id

    admin = await _get_admin()

    personal = (
        await admin.table("teams")
        .select("id")
        .eq("owner_id", user_id)
        .eq("is_personal", True)
        .limit(1)
        .execute()
    )
    if personal.data:
        tid = str(personal.data[0]["id"])
        logger.debug(f"[_resolve_team_id] Fallback to personal team: {tid}")
        return tid

    tm = (
        await admin.table("team_members")
        .select("team_id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    tid = str(tm.data[0]["team_id"]) if tm.data else None
    logger.debug(f"[_resolve_team_id] Fallback to first membership: {tid}")
    return tid


# ============================================
# Tag helpers
# ============================================


async def resolve_and_attach_tags(
    resource_id: str, tag_names: list[str], user_id: str
) -> list[str]:
    """Resolve tag names to IDs (auto-create if missing) and attach to resource."""
    repo = TagsRepository()
    tag_ids = []
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        tag = await repo.get_tag_by_name(name, user_id)
        if not tag:
            tag = await repo.create_tag(name=name, user_id=user_id)
        tag_ids.append(str(tag["id"]))
    if tag_ids:
        await repo.bulk_add_tags_to_resource(resource_id, tag_ids, source="manual")
    return tag_ids


# ============================================
# Download dispatch
# ============================================


async def dedup_and_dispatch(
    *,
    platform_id: str,
    user_id: str,
    resource_id: str | None,
    media_type: int,
    video_title: str,
    download_video: bool,
    download_cover: bool,
    url: str | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> dict:
    """Per-type Orchestrator dedup check + DBOS workflow dispatch.
    PR-D7 phase 3: was Celery .delay()."""
    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.download import download_workflow

    is_image_type = int(media_type) in (2, 68)

    requested = {}
    if download_video:
        requested["image" if is_image_type else "video"] = True
    if download_cover:
        requested["cover"] = True

    if not requested:
        return {
            "task_id": None,
            "types_submitted": [],
            "types_skipped": [],
            "types_subscribed": [],
        }

    types_to_download: list[str] = []
    types_subscribed: list[str] = []
    types_skipped: list[str] = []

    orchestrator = get_task_manager()
    for dtype in requested:
        try:
            result = await orchestrator.acquire_or_subscribe(
                task_type=f"download:{dtype}",
                dedup_identifier=platform_id,
                user_id=user_id,
                resource_id=resource_id or "",
            )
            action = result["action"]
            logger.info(f"[Download/Dedup] {dtype}={action} for {platform_id}")
            if action == "created":
                types_to_download.append(dtype)
            elif action == "subscribed":
                types_subscribed.append(dtype)
            else:
                types_skipped.append(dtype)
        except Exception as e:
            logger.warning(f"[Download/Dedup] {dtype} dedup failed, proceeding: {e}")
            types_to_download.append(dtype)

    task_id = None
    unified_task_id = None
    if types_to_download:
        dl_video = ("video" in types_to_download) or ("image" in types_to_download)
        dl_cover = "cover" in types_to_download

        # Pre-generate DBOS workflow_id so the task_tracking row exists
        # with dbos_workflow_id populated BEFORE the workflow's tracker
        # decorator fires. See media_fetch_helpers.parse path for the
        # rationale.
        import uuid as _uuid

        task_id = str(_uuid.uuid4())

        try:
            dl_parts = [t.capitalize() for t in types_to_download]
            dl_subtitle = " + ".join(dl_parts)
            unified_task_id = await orchestrator.create(
                user_id=user_id,
                task_type="download",
                title=video_title[:50] if video_title else platform_id,
                subtitle=dl_subtitle,
                media_id=platform_id,
                resource_id=resource_id,
                dbos_workflow_id=task_id,
            )
        except Exception as e:
            logger.warning(f"[Download/Dedup] Pre-create unified_task failed: {e}")

        try:
            logger.info(
                f"[Download/Init] DBOS dispatch: platform_id={platform_id}, "
                f"types={types_to_download}, user={user_id}, wf_id={task_id}"
            )
            await start_workflow_routed(
                "download",
                dbos_workflow_callable=download_workflow,
                dbos_workflow_kwargs={
                    "platform_id": platform_id,
                    "user_id": user_id,
                    "url": url,
                    "download_video": dl_video,
                    "download_cover": dl_cover,
                    "media_type": media_type,
                    "video_title": (video_title[:50] if video_title else "undefined"),
                    "resource_id": resource_id,
                },
                workflow_id=task_id,
            )
        except Exception as celery_err:
            logger.warning(f"[Download/Init] DBOS dispatch failed: {celery_err}")
            if background_tasks:
                from app.services.media.downloader.downloader import DownloaderService

                if dl_video:
                    if is_image_type:
                        background_tasks.add_task(
                            DownloaderService.download_images_by_platform_id,
                            platform_id,
                            user_id=user_id,
                        )
                    else:
                        background_tasks.add_task(
                            DownloaderService.download_video_by_platform_id,
                            platform_id,
                            user_id=user_id,
                        )
                if dl_cover:
                    background_tasks.add_task(
                        DownloaderService.download_cover_by_platform_id,
                        platform_id,
                        user_id=user_id,
                    )
                task_id = "background"

    return {
        "task_id": unified_task_id or task_id,
        "types_submitted": types_to_download,
        "types_skipped": types_skipped,
        "types_subscribed": types_subscribed,
    }


# ============================================
# Douyin parse fallback
# ============================================


async def douyin_parse_fallback(url: str, user_id: str) -> tuple[dict, str, str]:
    """Douyin parse fallback: LightHTTP → DrissionPage."""
    aweme_detail = None
    parse_method = "unknown"
    parse_method_name = "Unknown"
    fallback_reason = None

    user_parse_mode = "lighthttp"
    try:
        settings_repo = UserSettingsRepository()
        user_settings = await settings_repo.get_by_user_id(user_id)
        if user_settings and user_settings.get("settings_json"):
            user_parse_mode = user_settings["settings_json"].get(
                "parse_mode", "lighthttp"
            )
        logger.info(f"[Douyin Fallback] User {user_id} parse_mode: {user_parse_mode}")
    except Exception as e:
        logger.warning(f"Failed to read user parse mode, using default: {e}")

    # Pick a Douyin UA once per request so LightHTTP + BrowserAuto share it.
    from app.services.media.parsers.douyin_parse.ua_pool import pick_ua

    douyin_ua = pick_ua()

    if user_parse_mode == "drissionpage":
        try:
            logger.info(f"[BrowserAuto] User selected browser parsing: {url}")
            aweme_detail = await DrissionPageParser.fetch_one_video(
                url, user_agent=douyin_ua
            )
            if aweme_detail:
                parse_method = "browser_auto"
                parse_method_name = "BrowserAuto"
                logger.success("[BrowserAuto] Parse successful")
        except Exception as e:
            logger.error(f"[BrowserAuto] Parse failed: {e}")
            fallback_reason = f"BrowserAuto error: {str(e)[:50]}"
    else:
        try:
            logger.info(f"[LightHTTP] Attempting parse: {url}")
            aweme_detail = await IesDouyinParser.parse(url, user_agent=douyin_ua)
            if aweme_detail:
                parse_method = "light_http"
                parse_method_name = "LightHTTP"
                logger.success("[LightHTTP] Parse successful")
            else:
                fallback_reason = "LightHTTP returned empty result"
        except Exception as e:
            fallback_reason = f"LightHTTP error: {str(e)[:50]}"
            logger.warning(f"[LightHTTP] Parse failed: {e}")

        if not aweme_detail:
            try:
                logger.info(f"[BrowserAuto] Falling back to browser parsing: {url}")
                aweme_detail = await DrissionPageParser.fetch_one_video(
                    url, user_agent=douyin_ua
                )
                if aweme_detail:
                    parse_method = "browser_auto"
                    parse_method_name = "BrowserAuto"
                    logger.success("[BrowserAuto] Parse successful")
            except Exception as e:
                logger.error(f"[BrowserAuto] Parse failed: {e}")

    if not aweme_detail:
        raise HTTPException(
            status_code=404,
            detail="Cannot fetch video info (yt-dlp, LightHTTP and BrowserAuto all failed)",
        )

    parsed_data = await DouyinFormatter.parse_aweme_detail(
        aweme_detail=aweme_detail,
        valid_url=url,
        download_video=True,
        download_music=False,
        download_cover=True,
    )
    if not parsed_data:
        raise HTTPException(status_code=500, detail="Douyin video parsing failed")

    return parsed_data, parse_method, parse_method_name


async def mark_cookie_if_auth_failure(user_id: str, platform: str, error: str) -> None:
    """Mark a user's platform cookie as invalid when yt-dlp encounters auth errors."""
    auth_keywords = ["login", "401", "403", "cookie", "sign in", "authenticated"]
    if any(kw in error.lower() for kw in auth_keywords):
        try:
            from app.repositories.cookies_repository import CookiesRepository

            repo = CookiesRepository()
            await repo.mark_invalid(user_id, platform, error[:200])
            logger.info(
                f"[Cookie] Marked {platform} cookie as invalid for user {user_id}: "
                f"{error[:80]}"
            )
        except Exception as e:
            logger.warning(f"[Cookie] Failed to mark cookie invalid: {e}")


# ============================================
# yt-dlp fetch handler
# ============================================


async def handle_media_fetch_dispatch(
    url: ValidatedURL,
    platform: str,
    request: MediaFetchRequest,
    background_tasks: BackgroundTasks,
    auth: AuthDep,
    tags: Optional[list[str]] = None,
    tag_ids: Optional[list[str]] = None,
) -> dict:
    # Boundary: runtime guard. Caller must pass ValidatedURL (mypy not in CI).
    assert isinstance(url, ValidatedURL), (
        "handle_media_fetch_dispatch requires ValidatedURL — "
        "call validate_url_async at the API edge first"
    )
    """Unified entry for parse + dispatch across all supported platforms.

    Despite the legacy `handle_ytdlp_fetch` name (renamed to this), the
    function is NOT yt-dlp specific. Branch by platform:
      - douyin → DrissionPage / ABogus / LightHTTP fallback chain (httpx)
      - yt-dlp platforms → yt-dlp
    All branches end on the same DBOS parse_workflow dispatch path.

    PR-D7 phase 3: was Celery `parse_media_task.delay`. Now dispatches
    `parse_workflow` via DBOS."""
    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.parse import parse_workflow

    mgr = get_task_manager()

    has_cookie = False
    if platform in ("douyin", "bilibili", "youtube"):
        try:
            has_cookie = await YtdlpService.user_has_cookie(auth.user_id, platform)
            logger.info(
                f"[Cookie] Platform={platform}, user={auth.user_id}, "
                f"has_cookie={has_cookie}"
            )
        except Exception as e:
            logger.warning(f"[Cookie] Cookie check failed, proceeding without: {e}")

    skip_ytdlp = platform == "douyin" and not has_cookie

    # ── L2 dedup: per-user already-owned short-circuit ──
    # Master used to return "already downloaded" toast at parse entry
    # when the current user had a completed resource for this URL. The
    # DBOS port lost that — we'd run fetch+parse+dispatch+cache-check
    # only to land on the cache_hit short-circuit downstream. L2 puts
    # the check back at the front door so the user gets an instant
    # response and we save a parse pass + a worker hop.
    try:
        from app.repositories.resources_repository import ResourcesRepository

        owned = await ResourcesRepository().get_completed_resource_by_url_and_creator(
            url=url, creator_id=auth.user_id
        )
        if owned:
            background_tasks.add_task(
                log_user_action,
                user_id=auth.user_id,
                action="fetch",
                message=f"Already owned: {url[:40]}...",
                status="success",
                details={"platform": platform, "dedup_action": "already_owned"},
            )
            return {
                "success": True,
                "async": False,
                "message": "You already have this in your library",
                "dedup_action": "already_owned",
                "resource_id": str(owned.get("id")),
                "media_id": str(owned.get("media_id")),
            }
    except Exception as e:
        logger.debug(f"[L2/Dedup] probe failed (non-fatal): {e}")

    dedup_key = None
    try:
        result = await mgr.acquire_or_subscribe(
            task_type="parse",
            dedup_identifier=url,
            user_id=auth.user_id,
            resource_id="",
        )
        if result["action"] in ("subscribed", "completed"):
            return {
                "success": True,
                "async": True,
                "message": f"Parse already {result['action']}",
                "dedup_action": result["action"],
            }
        dedup_key = result.get("dedup_key")
    except Exception as e:
        logger.warning(f"[Parse/Dedup] check failed, proceeding: {e}")

    # ── L3 idempotency: deterministic DBOS workflow_id ──
    # Build the workflow_id from (user_id, sha1(url), 30-second bucket)
    # instead of a fresh UUID. Effect: a second click on the same link
    # within 30 s reuses the same DBOS workflow → DBOS returns the cached
    # result without re-running the body. Catches double-clicks, network
    # retries, and concurrent dispatch races that bypass L1/L2.
    #
    # The 30-s bucket is short enough that a deliberate re-parse 1 minute
    # later still gets a fresh workflow; long enough to swallow real
    # human / network retry windows.
    import hashlib
    import time

    bucket = int(time.time() // 30)
    url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    dbos_wf_id = f"parse-{auth.user_id[:8]}-{url_hash}-{bucket}"

    unified_task_id = dbos_wf_id  # PK on task_tracking is dbos_workflow_id
    try:
        unified_task_id = await mgr.create(
            user_id=auth.user_id,
            task_type="parse",
            title=f"Parse {url[:50]}",
            subtitle="Initializing...",
            dedup_key=dedup_key,
            dbos_workflow_id=dbos_wf_id,
        )
    except Exception as e:
        # Within the 30-s bucket the row may already exist — that's the
        # whole point of L3 (idempotent re-submit). Treat unique-violation
        # as success and let DBOS short-circuit the workflow body too.
        if "duplicate key" in str(e).lower() or "23505" in str(e):
            logger.info(
                f"[L3/Parse] idempotent re-submit, reusing wf_id={dbos_wf_id[:32]}"
            )
        else:
            logger.warning(f"[Parse] Pre-create unified_task failed: {e}")

    await start_workflow_routed(
        "parse",
        dbos_workflow_callable=parse_workflow,
        dbos_workflow_kwargs={
            "url": url,
            "user_id": auth.user_id,
            "video_bool": request.video_bool,
            "cover_bool": True,
            # Forward the parse-page tag picker selection so the workflow
            # can attach them to the new resource (was being silently
            # dropped, breaking the tag-driven AI chain).
            "tag_ids": tag_ids or [],
        },
        workflow_id=dbos_wf_id,
    )

    background_tasks.add_task(
        log_user_action,
        user_id=auth.user_id,
        action="fetch",
        message=f"Parse submitted: {url[:40]}...",
        status="success",
        details={"platform": platform, "async": True},
    )

    return {
        "success": True,
        "async": True,
        "message": "Parse task submitted",
        "task_id": unified_task_id,
    }


# Private aliases (backward-compat for any internal usage expecting underscore prefix)
_resolve_team_id = resolve_team_id
_resolve_and_attach_tags = resolve_and_attach_tags
_dedup_and_dispatch = dedup_and_dispatch
_douyin_parse_fallback = douyin_parse_fallback
_mark_cookie_if_auth_failure = mark_cookie_if_auth_failure
_handle_ytdlp_fetch = handle_media_fetch_dispatch  # legacy alias, drop after callers migrate
