"""Regression test for the Fetch Video re-parse step on yt-dlp platforms.

User requirement (2026-05-13): when the user clicks Fetch Video on a
bilibili / youtube / etc. record, the endpoint should re-parse the
page via yt-dlp before dispatching the download — so title, cover,
counters, and tags reflect the current state of the source (catches
taken-down videos early and keeps 资源库 metadata fresh).

The douyin / tiktok branch already does this (media_fetch_router.py
:236-272). This regression test pins the parallel yt-dlp branch so
nobody silently regresses it back.
"""

from __future__ import annotations

import importlib
import inspect


def test_fetch_media_by_type_reparses_yt_dlp_platforms() -> None:
    """Static check on the fetch_media_by_type handler: the yt-dlp
    re-parse branch must (a) call YtdlpService.fetch_metadata,
    (b) feed the result through _map_metadata_to_media, and
    (c) PATCH the parsed_media row via repo.update."""
    router_mod = importlib.import_module("app.api.media_fetch_router")
    source = inspect.getsource(router_mod.fetch_media_by_type)

    # The handler branches by platform: douyin path uses the unified
    # chain (reparse_douyin), yt-dlp path uses YtdlpService.
    assert "YtdlpService.fetch_metadata" in source, (
        "fetch_media_by_type must call YtdlpService.fetch_metadata in "
        "the non-douyin branch so bilibili / youtube etc. re-parse on "
        "every Fetch Video request — matches the douyin branch's "
        "unified-chain reparse_douyin call."
    )
    assert "YtdlpService._map_metadata_to_media" in source, (
        "fetch_media_by_type must feed the yt-dlp info_dict through "
        "_map_metadata_to_media to project it into the parsed_media "
        "schema before PATCHing."
    )
    # The re-parse must actually persist its findings — otherwise the
    # work is wasted. Look for an `await repo.update(...)` call within
    # the yt-dlp branch.
    yt_branch_start = source.index("YtdlpService.fetch_metadata")
    yt_branch_end = source.find(
        "dispatch_result = await dedup_and_dispatch", yt_branch_start
    )
    yt_branch_block = source[yt_branch_start:yt_branch_end]
    assert "await repo.update(platform_id, update_fields)" in yt_branch_block, (
        "yt-dlp re-parse must call repo.update(...) to persist the "
        "refreshed fields — otherwise title / cover / counters stay "
        "stale on bilibili / youtube records."
    )


def test_fetch_media_by_type_yt_dlp_reparse_swallows_errors() -> None:
    """Re-parse failures (network blip, yt-dlp can't reach the site)
    must NOT block the download dispatch. The handler should log and
    fall through to dedup_and_dispatch with the existing metadata —
    matches the douyin branch's defensive try/except pattern."""
    router_mod = importlib.import_module("app.api.media_fetch_router")
    source = inspect.getsource(router_mod.fetch_media_by_type)

    yt_branch_start = source.index("YtdlpService.fetch_metadata")
    yt_branch_end = source.find(
        "dispatch_result = await dedup_and_dispatch", yt_branch_start
    )
    yt_branch_block = source[yt_branch_start:yt_branch_end]

    # Must be wrapped in try/except — re-parse is best-effort.
    assert "except Exception" in yt_branch_block, (
        "yt-dlp re-parse must wrap its YtdlpService calls in "
        "try/except so a parse failure doesn't block the download "
        "dispatch (the download itself has further fallbacks)."
    )
