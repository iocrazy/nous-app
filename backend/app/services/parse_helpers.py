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


def fetch_and_parse(
    valid_url: str,
    video_bool: bool,
    cover_bool: bool,
    categories: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> tuple[dict, dict]:
    """DrissionPage fetch + DouyinFormatter parse.
    Returns (aweme_detail, parsed_data). Raises RuntimeError on failure."""
    from app.services.douyin_parse.drissionpage_parser import DrissionPageParser
    from app.services.douyin_parse.formatter import DouyinFormatter

    aweme_detail = _run_async(
        DrissionPageParser.fetch_one_video(valid_url, user_agent=user_agent)
    )
    if not aweme_detail:
        raise RuntimeError("Cannot fetch video info")

    parsed_data = _run_async(
        DouyinFormatter.parse_aweme_detail(
            aweme_detail=aweme_detail,
            valid_url=valid_url,
            download_video=video_bool,
            download_music=False,
            download_cover=cover_bool,
        )
    )
    if not parsed_data:
        raise RuntimeError("Parse failed")

    # `categories` was a user-supplied tag set in the legacy Celery
    # parse_task; the formatter never consumed it (it's persisted
    # separately via auto_tag_step / explicit tag dispatch). Keep
    # the kwarg in our signature for caller compatibility but stop
    # forwarding it to the formatter — that was a regression
    # introduced when parse_tasks.py was extracted into this helper.
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

    # MediaCreate carries `need_download_video` / `need_download_music`
    # / `need_download_cover` for the request schema, but those columns
    # don't live on parsed_media (download intent flows through
    # resources.{video,cover,image}_download_status instead). Strip
    # them before insert to avoid PGRST204 schema-cache errors.
    for k in ("need_download_video", "need_download_music", "need_download_cover"):
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
