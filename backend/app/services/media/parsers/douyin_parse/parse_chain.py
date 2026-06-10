# backend/app/services/media/parsers/douyin_parse/parse_chain.py

"""Unified Douyin parse chain: ABogus → DrissionPage.

Single source of truth for how a douyin share-URL / aweme_id becomes
``(aweme_detail, parsed_data)``. Initial parse (``parse_workflow`` via
``parse_helpers.fetch_and_parse``) and download-time re-parse
(``download_helpers`` / ``download_strategies`` / refetch endpoints) all
call this module, so the two paths can never fork again.

Why no LightHTTP (``IesDouyinParser``) tier (removed 2026-06-10):
- videos: the iesdouyin.com share page is permanently anti-bot blocked
  (every probe ends in NO_ROUTER_DATA), so the tier only burned time
- image notes: ABogus covers /note/ URLs end-to-end (verified live on
  prod — note 7641214325696253220 → media_type=68, image_download_urls=2)

The fork this module kills was a production P1: re-parse ran
LightHTTP → DrissionPage (no ABogus), always failed, and dropped douyin
downloads into the yt-dlp fallback whose format fallthrough grabs
HEVC → browser shows a black screen with audio only.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

# Admin toggles in system_settings. The legacy `douyin_lighthttp_enabled`
# key is intentionally gone — the tier no longer exists.
_METHOD_FLAG_KEYS: dict[str, str] = {
    "douyin_abogus_enabled": "abogus",
    "douyin_drissionpage_enabled": "drissionpage",
}


def _coerce_flag(value: Any) -> bool:
    """system_settings.value is jsonb — admins may have stored a JSON
    boolean or the string "true"; asyncpg/postgrest return native Python
    types, never coerce to str (see reference_system_settings_jsonb_typed)."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


async def _read_method_flag_rows() -> list[dict]:
    from app.db.supabase_client import get_async_supabase_admin

    client = await get_async_supabase_admin()
    result = await (
        client.table("system_settings")
        .select("key, value")
        .in_("key", list(_METHOD_FLAG_KEYS.keys()))
        .execute()
    )
    return result.data or []


async def get_douyin_method_flags() -> dict[str, bool]:
    """Read Douyin parse method toggles from system_settings. Defaults
    to all-on so a missing settings row doesn't disable parsing."""
    flags = {short: True for short in _METHOD_FLAG_KEYS.values()}
    try:
        for row in await _read_method_flag_rows():
            short = _METHOD_FLAG_KEYS.get(row.get("key", ""))
            if short is not None:
                flags[short] = _coerce_flag(row.get("value"))
    except Exception as e:
        logger.warning(f"[DouyinChain] failed to read method flags, all-on: {e}")
    return flags


async def fetch_douyin_detail(
    url_or_id: str,
    *,
    user_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    download_video: bool = True,
    download_music: bool = False,
    download_cover: bool = True,
    valid_url: Optional[str] = None,
) -> Optional[tuple[dict, dict, str]]:
    """Run the unified chain. Returns ``(aweme_detail, parsed_data, method)``
    with ``method`` in {"abogus", "drissionpage"}, or ``None`` if every
    enabled method fails.

    ``url_or_id`` may be a share URL or a bare aweme_id (digits) — ABogus
    resolves bare IDs directly; DrissionPage needs a URL and will simply
    fail through on a bare ID.
    """
    from app.services.media.parsers.douyin_parse.abogus_parser import (
        ABogusDouyinParser,
    )
    from app.services.media.parsers.douyin_parse.drissionpage_parser import (
        DrissionPageParser,
    )
    from app.services.media.parsers.douyin_parse.formatter import DouyinFormatter
    from app.services.media.parsers.douyin_parse.ua_pool import pick_ua

    if not user_agent:
        # One UA for the whole chain so the ABogus signature, API headers
        # and any later download request agree.
        user_agent = pick_ua()

    if not valid_url:
        valid_url = (
            url_or_id
            if "://" in url_or_id
            else f"https://www.douyin.com/video/{url_or_id}"
        )

    flags = await get_douyin_method_flags()
    methods: list[tuple[str, Any]] = []
    if flags.get("abogus", True):
        methods.append(("abogus", ABogusDouyinParser.parse))
    if flags.get("drissionpage", True):
        methods.append(("drissionpage", DrissionPageParser.fetch_one_video))
    if not methods:
        logger.warning("[DouyinChain] all parse methods disabled in admin settings")
        return None

    src_clip = str(url_or_id)[:80]
    for method, parse_fn in methods:
        try:
            aweme_detail = await parse_fn(
                url_or_id, user_id=user_id, user_agent=user_agent
            )
        except Exception as e:
            logger.warning(f"[DouyinChain] {method} raised for {src_clip}: {e}")
            continue
        if not aweme_detail:
            logger.info(f"[DouyinChain] {method} returned no detail for {src_clip}")
            continue
        try:
            parsed_data = await DouyinFormatter.parse_aweme_detail(
                aweme_detail=aweme_detail,
                valid_url=valid_url,
                download_video=download_video,
                download_music=download_music,
                download_cover=download_cover,
            )
        except Exception as e:
            logger.warning(f"[DouyinChain] formatter failed after {method}: {e}")
            continue
        if parsed_data:
            logger.info(f"[DouyinChain] succeeded via {method} for {src_clip}")
            return aweme_detail, parsed_data, method

    logger.warning(f"[DouyinChain] all enabled methods failed for {src_clip}")
    return None


async def reparse_douyin(
    platform_id: str,
    *,
    original_url: Optional[str] = None,
    user_id: Optional[str] = None,
    user_agent: Optional[str] = None,
    download_video: bool = True,
    download_music: bool = True,
    download_cover: bool = True,
) -> Optional[tuple[dict, str]]:
    """Download-time re-parse for fresh CDN URLs.

    Tries the stored ``original_url`` first, then falls back to the bare
    ``platform_id`` (aweme_id) — ABogus resolves digit IDs without the
    share-redirect hop, which replaces the old LightHTTP-directID hack.

    Returns ``(parsed_data, method)`` or ``None``.
    """
    sources = [
        src
        for src in (original_url, str(platform_id) if platform_id else None)
        if src
    ]
    for idx, src in enumerate(sources):
        if idx:
            logger.info(
                f"[DouyinChain] re-parse via URL failed, retrying with bare "
                f"aweme_id {src}"
            )
        result = await fetch_douyin_detail(
            src,
            user_id=user_id,
            user_agent=user_agent,
            download_video=download_video,
            download_music=download_music,
            download_cover=download_cover,
            valid_url=original_url,
        )
        if result:
            _detail, parsed_data, method = result
            return parsed_data, method
    return None
