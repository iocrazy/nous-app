"""Regression test for the DrissionPage fallback platform guard.

Background — 2026-05-13: a bilibili download was traced live on prod
and observed wasting ~30s in the BrowserAuto (DrissionPage) fallback
before bailing with empty parse data. DrissionPage's scraper targets
douyin's share-page DOM and the downstream DouyinFormatter expects
aweme_detail-shaped input — running them on bilibili / youtube / etc.
is dead-end work that delays the partial-fail raise PR #264 added.

This static check pins the guard so a future refactor doesn't lose
it. If someone wants to broaden the fallback to more platforms they
should add a platform-specific scraper, not silently re-enable the
douyin path for all platforms.
"""

from __future__ import annotations

import importlib
import inspect


def test_drissionpage_fallback_guarded_by_platform() -> None:
    """The douyin-chain recovery block (ABogus → DrissionPage re-parse,
    which replaced the old BrowserAuto fallback) in download_strategies
    must check source_platform before running — otherwise non-douyin
    tasks (bilibili / youtube / xhs / twitter) burn ~30s in a Chromium
    scrape that always returns empty."""
    strategies_mod = importlib.import_module("app.tasks.download_strategies")
    source = inspect.getsource(strategies_mod)

    # Locate the chain-recovery block by its log message signature.
    anchor = source.find("re-parsing via ")
    assert anchor != -1, (
        "douyin chain recovery log line moved or was removed — update "
        "this regression test if intentional."
    )

    # The platform guard must appear before the recovery log within the
    # same block. Take the 1200 chars before the anchor as the guard
    # zone (the if-condition + comment).
    guard_zone = source[max(0, anchor - 1200) : anchor + 200]

    assert "source_platform" in guard_zone, (
        "douyin chain recovery must check `source_platform` before "
        "running the douyin re-parse (which can end in DrissionPage). "
        "Without the guard, non-douyin tasks waste ~30s loading "
        "Chromium and always return empty — observed on prod 2026-05-13."
    )
    assert "douyin" in guard_zone, (
        "chain recovery guard must whitelist douyin (and optionally "
        "tiktok) explicitly — generic platform checks are a smell."
    )

    # The inverse guard: yt-dlp must NOT be reachable for douyin —
    # its format fallthrough grabs HEVC (black screen, audio only,
    # 2026-06-10 P1). The yt-dlp fallback lives in the elif branch.
    ytdlp_anchor = source.find("yt-dlp fallback (non-douyin platforms only)")
    assert ytdlp_anchor != -1 and ytdlp_anchor > anchor, (
        "yt-dlp fallback must be the non-douyin elif branch AFTER the "
        "douyin chain recovery — douyin must never reach yt-dlp."
    )


def test_drissionpage_import_inside_platform_branch() -> None:
    """Sanity follow-up: DrissionPageParser import must be inside the
    guarded branch, not unconditional at module top. Importing it
    eagerly would pull Chromium dependencies into every download
    worker even when no douyin task runs."""
    strategies_mod = importlib.import_module("app.tasks.download_strategies")
    source = inspect.getsource(strategies_mod)

    # The import must NOT appear at the top of the module — only
    # inside the conditional branch.
    top_lines = source.splitlines()[:50]
    for line in top_lines:
        assert "drissionpage_parser" not in line, (
            f"DrissionPageParser is imported at module top ({line!r}). "
            "Move the import inside the BrowserAuto fallback branch "
            "so unrelated workers don't pay the import cost."
        )
