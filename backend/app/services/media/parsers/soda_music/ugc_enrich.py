"""Best-effort douyin enrichment of qishui UGC videos.

A qishui UGC video's ``video_id`` IS a valid douyin aweme_id (the canonical
``https://www.douyin.com/video/<video_id>`` resolves to it). The qishui share
page carries NO engagement stats / publish time, but the douyin aweme detail
does. This module fetches the douyin aweme by that id and merges the missing
engagement stats + publish time into the parsed_data.

It is strictly best-effort: it NEVER raises and NEVER fails the UGC
parse/download. On any failure (no cookie, douyin down, ratelimit, parse miss)
it returns the input ``parsed_data`` unchanged.

Entry point choice: we already hold the canonical aweme_id, so we call
``IesDouyinParser._fetch_share_page(video_id, "video", ...)`` directly — this
skips the redirect round-trip that ``parse()`` does only to recover an id we
already have. User cookie / custom headers are loaded via
``_get_user_overrides(user_id)`` (the same helper ``parse()`` uses) and threaded
through as ``extra_headers``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger


async def enrich_ugc_with_douyin_stats(
    parsed_data: dict[str, Any],
    *,
    video_id: str,
    user_id: str | None,
    parser: Any | None = None,
) -> dict[str, Any]:
    """Best-effort: fetch the douyin aweme (qishui UGC video_id IS a douyin
    aweme_id) and merge engagement stats + publish time the qishui share page
    lacks. Returns a NEW dict (immutable). NEVER raises — returns parsed_data
    unchanged on any failure (no cookie, douyin down, ratelimit, parse miss).
    """
    try:
        if parser is None:
            from app.services.media.parsers.douyin_parse.ies_parser import (
                IesDouyinParser,
            )

            parser = IesDouyinParser

        # We already have the canonical aweme_id → fetch the share page directly
        # (skip the redirect round-trip parse() uses to recover an id).
        extra_headers = await parser._get_user_overrides(user_id)
        aweme = await parser._fetch_share_page(
            video_id, "video", extra_headers=extra_headers
        )
        if not aweme:
            return parsed_data

        out = dict(parsed_data)

        stats = aweme.get("statistics") or {}
        _set_if_present(out, "like_count", stats.get("digg_count"))
        _set_if_present(out, "comment_count", stats.get("comment_count"))
        _set_if_present(out, "share_count", stats.get("share_count"))
        _set_if_present(out, "favorite_count", stats.get("collect_count"))

        create_time = aweme.get("create_time")
        if create_time:
            try:
                out["published_at"] = datetime.fromtimestamp(
                    int(create_time), tz=timezone.utc
                )
            except (TypeError, ValueError, OSError):
                # Bad/overflowing timestamp — leave published_at untouched.
                pass

        return out
    except Exception as exc:  # noqa: BLE001 — best-effort, never fail the parse.
        logger.warning(
            f"[ugc_enrich] douyin enrichment skipped for video_id={video_id}: {exc}"
        )
        return parsed_data


def _set_if_present(out: dict[str, Any], key: str, value: Any) -> None:
    """Set ``out[key] = value`` only when ``value is not None``."""
    if value is not None:
        out[key] = value
