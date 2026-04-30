"""Pure helpers for the parse workflow — extracted from the legacy
`app.tasks.parse_tasks` (PR-D7 phase 3 cleanup).

These are non-Celery functions used by `app.workflows.parse`. Keeping
them here so the legacy `app/tasks/parse_tasks.py` can be deleted
without losing the helper logic.

Each helper preserves the exact behaviour of its `_xxx` predecessor.
The only API surface change is the import path:
    OLD: from app.tasks.parse_tasks import _extract_url
    NEW: from app.services.parse_helpers import extract_url
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

from app.core.utils import Utils
from app.services.classification_service import ClassificationService


def _run_async(coro):
    """Run an async coroutine in a sync context. Mirrors
    `app.tasks.utils.run_async`."""
    return asyncio.run(coro)


def extract_url(url: str) -> str:
    """Validate + canonicalise URL. Raises ValueError on bad input."""
    valid_urls = Utils.extract_valid_url(url)
    return valid_urls[0]


_DOUYIN_METHOD_FLAG_KEYS = {
    "douyin_lighthttp_enabled": "lighthttp",
    "douyin_abogus_enabled": "abogus",
    "douyin_drissionpage_enabled": "drissionpage",
}


def _get_douyin_method_flags() -> dict[str, bool]:
    """Read Douyin parse method toggles from system_settings. Defaults
    to all-on so a missing settings row doesn't disable parsing."""
    flags = {short: True for short in _DOUYIN_METHOD_FLAG_KEYS.values()}
    try:
        from app.db.supabase_client import get_async_supabase_admin

        async def _read():
            client = await get_async_supabase_admin()
            return await (
                client.table("system_settings")
                .select("key, value")
                .in_("key", list(_DOUYIN_METHOD_FLAG_KEYS.keys()))
                .execute()
            )

        result = _run_async(_read())
        for row in result.data or []:
            short = _DOUYIN_METHOD_FLAG_KEYS.get(row["key"])
            if short:
                flags[short] = row["value"] is True or row["value"] == "true"
    except Exception as e:
        logger.warning(f"[Douyin] Failed to read method flags, using defaults: {e}")
    return flags


def _try_lighthttp(
    url: str,
    user_id: Optional[str],
    user_agent: Optional[str],
    video_bool: bool,
    cover_bool: bool,
):
    """LightHTTP via IesDouyinParser. Returns (aweme_detail, parsed_data) or None."""
    from app.services.douyin_parse.formatter import DouyinFormatter
    from app.services.douyin_parse.ies_parser import IesDouyinParser

    try:
        aweme_detail = _run_async(
            IesDouyinParser.parse(url, user_id=user_id, user_agent=user_agent)
        )
        if aweme_detail:
            parsed = _run_async(
                DouyinFormatter.parse_aweme_detail(
                    aweme_detail=aweme_detail,
                    valid_url=url,
                    download_video=video_bool,
                    download_music=False,
                    download_cover=cover_bool,
                )
            )
            if parsed:
                return aweme_detail, parsed
    except Exception as e:
        logger.warning(f"[Douyin] LightHTTP failed: {e}")
    return None


def _try_abogus(
    url: str,
    user_id: Optional[str],
    user_agent: Optional[str],
    video_bool: bool,
    cover_bool: bool,
):
    """ABogus signed HTTP via ABogusDouyinParser."""
    from app.services.douyin_parse.abogus_parser import ABogusDouyinParser
    from app.services.douyin_parse.formatter import DouyinFormatter

    try:
        aweme_detail = _run_async(
            ABogusDouyinParser.parse(url, user_id=user_id, user_agent=user_agent)
        )
        if aweme_detail:
            parsed = _run_async(
                DouyinFormatter.parse_aweme_detail(
                    aweme_detail=aweme_detail,
                    valid_url=url,
                    download_video=video_bool,
                    download_music=False,
                    download_cover=cover_bool,
                )
            )
            if parsed:
                return aweme_detail, parsed
    except Exception as e:
        logger.warning(f"[Douyin] ABogus failed: {e}")
    return None


def _try_drissionpage(
    url: str,
    user_id: Optional[str],
    user_agent: Optional[str],
    video_bool: bool,
    cover_bool: bool,
):
    """DrissionPage browser fallback (slow, last resort)."""
    from app.services.douyin_parse.drissionpage_parser import DrissionPageParser
    from app.services.douyin_parse.formatter import DouyinFormatter

    try:
        aweme_detail = _run_async(
            DrissionPageParser.fetch_one_video(
                url, user_id=user_id, user_agent=user_agent
            )
        )
        if aweme_detail:
            parsed = _run_async(
                DouyinFormatter.parse_aweme_detail(
                    aweme_detail=aweme_detail,
                    valid_url=url,
                    download_video=video_bool,
                    download_music=False,
                    download_cover=cover_bool,
                )
            )
            if parsed:
                return aweme_detail, parsed
    except Exception as e:
        logger.warning(f"[Douyin] DrissionPage failed: {e}")
    return None


def fetch_and_parse(
    valid_url: str,
    video_bool: bool,
    cover_bool: bool,
    categories: Optional[str] = None,
    user_agent: Optional[str] = None,
    user_id: Optional[str] = None,
) -> tuple[dict, dict]:
    """3-tier Douyin fallback chain (mirrors master/parse_tasks): LightHTTP →
    ABogus → DrissionPage, respecting admin toggles in system_settings.

    LightHTTP handles image-text notes (douyin /note/...) that
    DrissionPage's video-page-only flow misses. Browser is the slow
    last resort.

    Returns (aweme_detail, parsed_data). Raises RuntimeError on failure."""
    flags = _get_douyin_method_flags()
    logger.info(f"[Douyin Fallback] flags={flags} ua={(user_agent or '')[:40]}…")

    methods: list[tuple[str, Any]] = []
    if flags.get("lighthttp"):
        methods.append(("LightHTTP", _try_lighthttp))
    if flags.get("abogus"):
        methods.append(("ABogus", _try_abogus))
    if flags.get("drissionpage"):
        methods.append(("DrissionPage", _try_drissionpage))

    if not methods:
        raise RuntimeError("All Douyin parse methods are disabled in admin settings")

    aweme_detail = None
    parsed_data = None
    for name, fn in methods:
        logger.info(f"[Douyin Fallback] Trying {name}…")
        result = fn(valid_url, user_id, user_agent, video_bool, cover_bool)
        if result:
            aweme_detail, parsed_data = result
            logger.info(f"[Douyin Fallback] Succeeded via {name}")
            break

    if not parsed_data:
        raise RuntimeError("All enabled Douyin parse methods failed")

    # `categories` was a user-supplied tag set in the legacy Celery
    # parse_task; the formatter never consumed it (it's persisted
    # separately via auto_tag_step / explicit tag dispatch).
    if categories:
        parsed_data["_pending_categories"] = categories

    return aweme_detail, parsed_data


def save_media_to_db(
    parsed_data: dict, platform_id: str, video_bool: bool
) -> Optional[dict]:
    """Insert or update parsed_media row. Returns saved record or None."""
    from app.core.enums import DownloadStatus
    from app.repositories.media_repository import MediaRepository
    from app.schemas.media import MediaCreate

    repo = MediaRepository()
    existing = _run_async(repo.get_by_platform_id(platform_id))

    try:
        video_data = MediaCreate(**parsed_data)
        data_dict = video_data.model_dump()
    except Exception as e:
        logger.error(f"Data validation failed: {e}")
        return None

    # MediaCreate carries request-shape fields that don't live on
    # parsed_media. Strip before insert to avoid PGRST204 schema-cache
    # errors. user_id moved to resources.creator_id (per-user ownership)
    # — parse_workflow stuffs it onto parsed_data for downstream
    # MediaService.process_video to pick up, but it must not land in
    # the parsed_media INSERT itself.
    for k in (
        "need_download_video",
        "need_download_music",
        "need_download_cover",
        "user_id",
    ):
        data_dict.pop(k, None)
    # Internal helper marker added in fetch_and_parse — don't persist.
    data_dict.pop("_pending_categories", None)

    data_dict["video_download_status"] = (
        DownloadStatus.PENDING.value if video_bool else DownloadStatus.SKIPPED.value
    )
    data_dict["music_download_status"] = DownloadStatus.SKIPPED.value

    if existing:
        saved = _run_async(repo.update(platform_id, data_dict))
        logger.info(f"[Parse] Updated metadata: {platform_id}")
    else:
        saved = _run_async(repo.create(data_dict))
        logger.info(f"[Parse] Created metadata: {platform_id}")
    return saved


def auto_tag_media(
    video_db_id: Any,
    platform_id: str,
    aweme_detail: dict,
    title: str,
    description: str,
) -> None:
    """Best-effort hashtag → tag classification. Never raises."""
    try:
        original_tags: list[str] = []
        text_extra = aweme_detail.get("text_extra", []) or []
        if text_extra:
            original_tags = [
                tag.get("hashtag_name", "")
                for tag in text_extra
                if tag.get("hashtag_name")
            ]

        added_tags = _run_async(
            ClassificationService.auto_tag_media(
                media_id=video_db_id,
                title=title or "",
                description=description,
                original_tags=original_tags,
            )
        )
        if added_tags:
            logger.info(
                f"[Parse] Auto-tagged {platform_id} with {len(added_tags)} tags"
            )
    except Exception as e:
        logger.warning(f"[Parse] Auto-tagging failed for {platform_id}: {e}")
