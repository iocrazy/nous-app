"""Pure helpers for the parse workflow — extracted from the legacy
`app.tasks.parse_tasks` (PR-D7 phase 3 cleanup).

These are non-Celery functions used by `app.workflows.parse`. Keeping
them here so the legacy `app/tasks/parse_tasks.py` can be deleted
without losing the helper logic.

Each helper preserves the exact behaviour of its `_xxx` predecessor.
The only API surface change is the import path:
    OLD: from app.tasks.parse_tasks import _extract_url
    NEW: from app.services.media.parsers.parse_helpers import extract_url
"""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any, Optional

from loguru import logger

from app.core.utils import Utils
from app.services.ai.visual.classification_service import ClassificationService


def _run_async(coro):
    """Run an async coroutine in a sync context. Mirrors
    `app.tasks.utils.run_async` — now loop-safe (two branches).

    Branch 1 (no running loop — today's only path, since every caller is a
    sync parser helper): ``copy_context().run(asyncio.run, coro)``. The
    copy_context is a no-op for scope (asyncio.run already copies the calling
    thread's context) but kept explicit/symmetric.

    Branch 2 (a loop IS already running on this thread): a plain
    ``asyncio.run`` would raise ``RuntimeError: asyncio.run() cannot be called
    from a running event loop``. Hop to a worker thread (fresh loop) carrying
    the copied context — same fix as ``app.tasks.utils.run_async`` branch 2.
    Was previously single-branch (latent crash if ever reached from an async
    context); hardened defensively even though no current caller hits it.
    """
    import concurrent.futures

    ctx = contextvars.copy_context()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(ctx.run, asyncio.run, coro).result()
    return ctx.run(asyncio.run, coro)


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
    user_id: Optional[str] = None,
) -> tuple[dict, dict, str]:
    """Unified Douyin parse chain: ABogus → DrissionPage (see
    ``douyin_parse.parse_chain`` for the chain itself + admin toggles).

    ABogus authenticates with the user's saved douyin cookie via the
    user_cookies table, covers videos AND image-text notes, and is the
    same chain the download-time re-parse uses — the initial-parse /
    re-parse fork (and its dead LightHTTP tier) is gone.

    Returns (aweme_detail, parsed_data, parse_method). Raises
    RuntimeError on failure."""
    from app.services.media.parsers.douyin_parse.parse_chain import (
        fetch_douyin_detail,
    )

    result = _run_async(
        fetch_douyin_detail(
            valid_url,
            user_id=user_id,
            user_agent=user_agent,
            download_video=video_bool,
            download_music=False,
            download_cover=cover_bool,
        )
    )
    if not result:
        raise RuntimeError("All enabled Douyin parse methods failed")
    aweme_detail, parsed_data, parse_method = result

    # `categories` was a user-supplied tag set in the legacy Celery
    # parse_task; the formatter never consumed it (it's persisted
    # separately via auto_tag_step / explicit tag dispatch).
    if categories:
        parsed_data["_pending_categories"] = categories

    return aweme_detail, parsed_data, parse_method


def fetch_and_parse_ytdlp(
    valid_url: str,
    video_bool: bool,
    cover_bool: bool,
    user_agent: Optional[str] = None,
    user_id: Optional[str] = None,
) -> tuple[dict, dict]:
    """yt-dlp parse path for non-douyin platforms (bilibili / youtube / etc).

    Returns the same `(aweme_detail, parsed_data)` tuple shape as
    `fetch_and_parse` so the workflow keeps a single contract. There is no
    real aweme_detail outside the douyin chain — instead we synthesise a
    minimal stub that surfaces yt-dlp's `tags` list under the `text_extra`
    key auto_tag_media reads, so hashtag → tag classification keeps working
    unchanged across platforms."""
    from app.boundary import validate_url
    from app.services.media.parsers.ytdlp_service import YtdlpService

    validated = validate_url(valid_url)
    ytdlp_info = _run_async(
        YtdlpService.fetch_metadata(validated, user_id=user_id, user_agent=user_agent)
    )
    parsed_data = YtdlpService._map_metadata_to_media(ytdlp_info, valid_url)

    parsed_data["need_download_video"] = video_bool
    parsed_data["need_download_cover"] = cover_bool
    parsed_data["need_download_music"] = False

    tags = ytdlp_info.get("tags") or []
    aweme_detail_stub: dict = {
        "text_extra": [{"hashtag_name": str(t)} for t in tags if t],
    }

    return aweme_detail_stub, parsed_data


def fetch_and_parse_qishui(
    valid_url: str,
    video_bool: bool,
    cover_bool: bool,
    user_agent=None,
    user_id=None,
):
    """Qishui (Soda) parse: resolve track metadata. Returns (aweme_detail, parsed_data)."""
    from app.services.media.parsers.soda_music.parse_entry import (
        resolve_qishui_metadata,
    )

    parsed_data = _run_async(resolve_qishui_metadata(url=valid_url, user_id=user_id))
    return {}, parsed_data


def save_media_to_db(
    parsed_data: dict, platform_id: str, video_bool: bool
) -> Optional[dict]:
    """Save parsed_media + create/update per-user resource via the canonical
    two-layer write. Delegates to MediaService.save_metadata_only so the
    DBOS workflow path matches master's Celery path (which D7 phase 3
    accidentally bypassed by reaching past MediaService directly into the
    repo, leaving 'video' downloads with no `resources` row → empty
    "我的下载" page).

    Returns the dict from MediaService.save_metadata_only with at least:
        success, platform_id, id (parsed_media.id), resource_id, dedup_hit.

    Returns None on validation failure (caller treats it as soft failure)."""
    from app.services.media.parsers.media_service import MediaService

    # MediaService consumes need_download_* flags and user_id; ensure the
    # video/cover request bits are present (auto_tag / categories live
    # in our own helper marker, strip before passing through).
    payload = dict(parsed_data)
    payload.pop("_pending_categories", None)
    payload.setdefault("need_download_video", video_bool)
    payload.setdefault("need_download_cover", True)
    payload.setdefault("need_download_music", False)

    try:
        result = _run_async(MediaService.save_metadata_only(platform_id, payload))
    except Exception as e:
        logger.error(f"[Parse] save_metadata_only raised: {e!r}")
        return None

    if not result.get("success"):
        logger.error(
            f"[Parse] save_metadata_only failed for {platform_id}: "
            f"{result.get('message')}"
        )
        return None

    logger.info(
        f"[Parse] Saved metadata for {platform_id}: "
        f"media_id={result.get('id')} resource_id={result.get('resource_id')} "
        f"dedup_hit={result.get('dedup_hit')}"
    )
    return result


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
