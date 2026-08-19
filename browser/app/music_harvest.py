"""Harvest the 「选择音乐」 panel's charts for one account. One run, one page.

Why this is its own flow and not a `/session/probe` call
=======================================================
`/session/probe` can already click those tabs — it is how they were measured
[实测 2026-08-19]. It must not be what *ships* them, for two independent
reasons:

1. **Its report redacts long digit runs.** That is exactly the shape of a
   `music_id`, so the one field this whole feature is about would come back
   masked. A recon report is built to describe a page without quoting it; a
   harvest has to quote it.
2. **Its activation vocabulary is a closed allow-list** whose stated job is
   keeping recon away from anything that commits. Widening it to carry a
   product feature's traffic would spend a safety boundary to save writing a
   flow.

What the platform decides, and the one guard that puts back
==========================================================
The tab captions are NOT ours: they come from the panel's own
`music/category` response, and we click whatever it names. That is the only
way to stay correct when the tab row changes — and it hands the platform the
ability to name a control we then press.

So `caption_refusal` refuses any caption that reads as committing (发布 /
确定 / 完成 / 提交 / 删除 …) **before the page is touched**, and the refusal
is reported rather than skipped silently. A renamed tab therefore costs one
missing chart, never an accidental publish.

The shape of a run
==================
navigate → seed one image (the editor does not exist before an upload; measured
2026-08-19, a run with no seed never leaves `/upload`) → open the panel →
record what it fetched on open (categories + 推荐) → take each remaining tab →
one `ChartRead` apiece.

**Nothing is typed.** The tab row only exists before a search: once a query has
been entered the panel shows results and the captions are gone.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Sequence

from .assets import AssetError, stage_assets
from .browser_runtime import (
    ProxyConfigError,
    apply_stealth,
    build_context_kwargs,
    build_launch_kwargs,
)
from .config import get_settings
from .dom import visible_marker_texts
from .inspect import InspectSpec, seed_file_input, session_refusal
from .platforms.douyin_music_charts import (
    ChartRead,
    HarvestResult,
    MusicCategory,
    MusicChartRecorder,
    build_chart,
    plan_tabs,
    read_categories,
)
from .redaction import scrub

logger = logging.getLogger("nous_browser.music_harvest")

SEED_ROLE = "seed:0"

#: Captions this flow will never click, whatever the platform calls a tab.
#: Substring match, deliberately: 「立即发布」 contains 发布 and is exactly the
#: thing that must not be pressed. The cost of being over-broad is a skipped
#: chart, which is reported; the cost of being under-broad is a published post.
COMMITTING_CAPTIONS: tuple[str, ...] = (
    "发布",
    "确定",
    "确认",
    "完成",
    "提交",
    "保存",
    "删除",
    "使用",
    "下一步",
)

#: A caption longer than this is not a tab. A bound on what a renamed tab row
#: can talk us into resolving on the page.
MAX_CAPTION_CHARS = 12

#: The chart the panel delivers without being asked [实测 2026-08-19].
OPENED_WITH_KEY = "recommend:1"

_WHITESPACE = re.compile(r"\s+")


def caption_refusal(caption: str) -> str | None:
    """`None` when this caption may be clicked, else why not. Pure, total.

    Runs before the page is touched — the same ordering `probe_actions`
    established, and for the same reason: a check that runs after the click has
    already resolved a locator is a description, not a guard.
    """
    text = _WHITESPACE.sub(" ", caption or "").strip()
    if not text:
        return "empty caption"
    if len(text) > MAX_CAPTION_CHARS:
        return f"caption is {len(text)} chars; tabs are short"
    for word in COMMITTING_CAPTIONS:
        if word in text:
            return f"caption contains committing word {word!r}"
    return None


async def _click_caption(page: Any, caption: str, *, timeout_ms: int) -> str:
    """Click the tab captioned exactly `caption`. Returns "" or why not.

    Plain `.click()` only — no `force`, no DOM-level `el.click()`. Both exist
    in the publish driver for controls the platform styles as non-interactive,
    and both bypass the guarantee that makes this safe: that the thing was a
    visible, enabled control where we thought it was.
    """
    try:
        locator = page.get_by_text(caption, exact=True)
        matches = int(await locator.count())
    except Exception as exc:  # noqa: BLE001
        return f"could not resolve: {type(exc).__name__}"
    if matches <= 0:
        return "no node renders exactly this caption"
    try:
        await locator.first.click(timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        return f"click failed: {type(exc).__name__}"
    return ""


async def harvest_charts(
    page: Any,
    *,
    open_dialog: Any,
    settle_ms: int,
    click_timeout_ms: int,
    tab_settle_ms: int,
    limit: int,
) -> HarvestResult:
    """Open the panel and take every tab. Total — never raises.

    `open_dialog` is injected rather than imported so this is drivable by a
    fake page in tests; the production caller passes
    `douyin_publish._open_music_dialog`.
    """
    result = HarvestResult()
    recorder = MusicChartRecorder()
    recorder.attach(page)
    try:
        recorder.phase(OPENED_WITH_KEY)
        entry_index = await open_dialog(page)
        if entry_index is None:
            result.categories_error = "could not open the music panel"
            return result
        await page.wait_for_timeout(settle_ms)
        await recorder.settle()

        categories = read_categories(recorder.category_payload)
        if not categories:
            # NOT "there are no charts". We do not know what the charts are,
            # and a caller must not overwrite a stored tab list with this.
            result.categories_error = "the panel never named its tabs"
        result.categories = tuple(categories)

        # The chart that arrived without being asked for.
        opened = next(
            (c for c in categories if c.key == OPENED_WITH_KEY),
            MusicCategory(category_id="1", name="推荐", kind="recommend"),
        )
        result.charts.append(build_chart(opened, recorder.list_payload(OPENED_WITH_KEY)))

        for category in plan_tabs(categories, opened_with=OPENED_WITH_KEY, limit=limit):
            refusal = caption_refusal(category.name)
            if refusal is not None:
                result.charts.append(
                    ChartRead(category=category, ok=False, error=f"refused: {refusal}")
                )
                continue
            recorder.phase(category.key)
            failure = await _click_caption(
                page, category.name, timeout_ms=click_timeout_ms
            )
            if failure:
                result.charts.append(
                    ChartRead(category=category, ok=False, error=failure)
                )
                continue
            try:
                await page.wait_for_timeout(tab_settle_ms)
            except Exception:  # noqa: BLE001
                pass
            await recorder.settle()
            result.charts.append(
                build_chart(category, recorder.list_payload(category.key))
            )
        return result
    finally:
        recorder.detach()


async def run_harvest(spec: InspectSpec, request: Any) -> Any:
    """One account, one browser, one page. Total — every failure is typed."""
    # patchright, not playwright: `test_patchright_everywhere` enforces it.
    from patchright.async_api import async_playwright

    from .schemas import MusicHarvestResponse, SessionStatus

    settings = get_settings()

    def failure(message: str, **detail: Any) -> Any:
        return MusicHarvestResponse(
            success=False,
            status=SessionStatus.FAILED,
            message=message,
            detail={"platform": spec.platform, **detail},
        )

    try:
        launch_kwargs = build_launch_kwargs(request.environment)
    except ProxyConfigError as exc:
        return failure(scrub(str(exc)), stage="proxy_config", reason="proxy_failed")

    context_kwargs = build_context_kwargs(request.environment, request.storage_state)
    started = time.monotonic()
    staged_items = [(SEED_ROLE, request.seed_file)]

    try:
        async with stage_assets(staged_items) as staged:
            seed_path = staged[SEED_ROLE].path
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(**launch_kwargs)
                try:
                    context = await browser.new_context(**context_kwargs)
                    await apply_stealth(context)
                    page = await context.new_page()
                    await page.goto(
                        request.url,
                        wait_until="domcontentloaded",
                        timeout=settings.nav_timeout_ms,
                    )
                    await page.wait_for_timeout(request.settle_ms)

                    visible_login = await visible_marker_texts(
                        page, spec.login_text_markers
                    )
                    lost = session_refusal(page.url, spec.allowed_hosts, visible_login)
                    if lost is not None:
                        return MusicHarvestResponse(
                            success=False,
                            status=SessionStatus.SESSION_INVALID,
                            message=lost,
                            detail={
                                "platform": spec.platform,
                                "stage": "session",
                                "url_after": scrub(page.url),
                            },
                        )

                    # The editor does not exist before an upload; a run with no
                    # seed never leaves `/upload` (measured). This is the one
                    # side effect the harvest has, and it is why the caller
                    # schedules it rather than running it per page view.
                    await seed_file_input(
                        page,
                        selector=request.seed_selector,
                        index=0,
                        wait_ms=request.seed_wait_ms,
                        paths=[seed_path],
                    )

                    from .platforms.douyin_publish import _open_music_dialog

                    async def open_dialog(target: Any) -> int | None:
                        return await _open_music_dialog(
                            target, request.click_timeout_ms, request.settle_ms
                        )

                    harvest = await harvest_charts(
                        page,
                        open_dialog=open_dialog,
                        settle_ms=request.panel_settle_ms,
                        click_timeout_ms=request.click_timeout_ms,
                        tab_settle_ms=request.tab_settle_ms,
                        limit=request.max_charts,
                    )
                    storage = await context.storage_state()
                    return _respond(spec, harvest, storage, time.monotonic() - started)
                finally:
                    await browser.close()
    except AssetError as exc:
        return failure(scrub(str(exc)), stage="seed", reason="asset_failed")
    except Exception as exc:  # noqa: BLE001 - total by contract
        logger.exception("music harvest raised for platform=%s", spec.platform)
        return failure(
            scrub(f"{type(exc).__name__}: {exc}"), stage="run", reason="unexpected"
        )


def _respond(
    spec: InspectSpec, harvest: HarvestResult, storage: Any, elapsed_s: float
) -> Any:
    from .schemas import (
        MusicChartPayload,
        MusicHarvestResponse,
        MusicSongPayload,
        SessionStatus,
    )

    charts = [
        MusicChartPayload(
            category_id=chart.category.category_id,
            category_name=chart.category.name,
            category_kind=chart.category.kind,
            ok=chart.ok,
            error=chart.error,
            cursor=chart.cursor,
            has_more=chart.has_more,
            songs=[
                MusicSongPayload(
                    music_id=song.music_id,
                    music_name=song.name,
                    music_author=song.author,
                    duration_s=song.duration_s,
                    user_count=song.user_count,
                    cover_url=song.cover_url,
                    play_url=song.play_url,
                )
                for song in chart.songs
            ],
        )
        for chart in harvest.charts
    ]
    read = sum(1 for chart in harvest.charts if chart.ok)
    return MusicHarvestResponse(
        # A run that read NOTHING is not a success, however cleanly it ended:
        # the caller's next move (keep the previous snapshot vs. store this
        # one) depends on the difference, and "no charts" and "no data" are
        # the two states this whole module keeps apart.
        success=read > 0,
        status=SessionStatus.SESSION_VALID if read > 0 else SessionStatus.FAILED,
        message=(
            f"read {read} of {len(charts)} charts"
            if read > 0
            else (harvest.categories_error or "no chart could be read")
        ),
        detail={
            "platform": spec.platform,
            "elapsed_s": round(elapsed_s, 1),
            "charts_read": read,
            "charts_attempted": len(charts),
            "categories_error": harvest.categories_error,
        },
        charts=charts,
        updated_storage_state=storage,
    )


__all__ = [
    "COMMITTING_CAPTIONS",
    "MAX_CAPTION_CHARS",
    "OPENED_WITH_KEY",
    "caption_refusal",
    "harvest_charts",
    "run_harvest",
]
