"""The harvest flow: open the panel, take every tab, attribute every list.

The risk this file is mostly about
==================================
The tab captions are **not ours**. They come from the panel's own
`music/category` response and we click whatever it names — which is the only
way to stay correct when the tab row changes, and which hands the platform the
ability to name a control we then press.

So the tests that matter most here are not the happy path. They are:

* a tab renamed to something that commits is refused **and never clicked**;
* a tab whose click failed reports that, and does not inherit the previous
  tab's songs;
* a panel that never opened is a typed failure, not an empty catalogue.
"""

from __future__ import annotations

import json

from app.music_harvest import OPENED_WITH_KEY, caption_refusal, harvest_charts
from app.platforms.douyin_music_charts import (
    CATEGORY_URL_FRAGMENT,
    LIST_URL_FRAGMENT,
)

CATEGORIES = {
    "categories": [
        {"category_id": "1", "category_name": "推荐", "type": "recommend"},
        {"category_id": "7088298745502646280", "category_name": "热门榜", "type": "rank"},
        {"category_id": "1", "category_name": "收藏", "type": "fav"},
    ],
    "status_code": 0,
}

EMPTY_FAVOURITES = {"extra": {"now": 1}, "status_code": 0, "status_msg": ""}


def _chart(music_id: str, name: str) -> dict:
    return {
        "cursor": "20",
        "has_more": True,
        "songs": [
            {
                "cover_url": "https://p26.douyinpic.com/x.jpeg",
                "duration": 27,
                "music_author": "A",
                "music_id": music_id,
                "music_name": name,
                "play_url": "https://lf26-music-east.douyinstatic.com/x.mp3",
                "user_count": 12,
            }
        ],
        "status_code": 0,
    }


class _Response:
    def __init__(self, url: str, payload: dict) -> None:
        self.url = url
        self.status = 200
        self._body = json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


class _Locator:
    def __init__(self, page: "_Page", caption: str) -> None:
        self._page = page
        self._caption = caption

    async def count(self) -> int:
        return 1 if self._caption in self._page.captions else 0

    @property
    def first(self) -> "_Locator":
        return self

    async def click(self, timeout: int = 0) -> None:
        self._page.clicks.append(self._caption)
        if self._caption in self._page.click_raises:
            raise TimeoutError("element is not stable")
        payload = self._page.tab_payloads.get(self._caption)
        if payload is not None:
            self._page.emit(_Response(f"https://x{LIST_URL_FRAGMENT}?c=1", payload))


class _Page:
    """Only the surface `harvest_charts` touches."""

    def __init__(self, *, captions: set[str], tab_payloads: dict[str, dict]) -> None:
        self.captions = captions
        self.tab_payloads = tab_payloads
        self.click_raises: set[str] = set()
        self.clicks: list[str] = []
        self.handlers: list = []

    def on(self, event: str, handler) -> None:
        if event == "response":
            self.handlers.append(handler)

    def remove_listener(self, event: str, handler) -> None:
        if event == "response" and handler in self.handlers:
            self.handlers.remove(handler)

    def emit(self, response) -> None:
        for handler in list(self.handlers):
            handler(response)

    def get_by_text(self, caption: str, exact: bool = False):
        return _Locator(self, caption)

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


def _page(**kwargs) -> _Page:
    return _Page(**kwargs)


def _opener(page: _Page, *, opens: bool = True, chart: dict | None = None):
    """Stands in for `_open_music_dialog`; emits what opening really fires."""

    async def open_dialog(target: _Page):
        if not opens:
            return None
        target.emit(_Response(f"https://x{CATEGORY_URL_FRAGMENT}", CATEGORIES))
        target.emit(
            _Response(
                f"https://x{LIST_URL_FRAGMENT}?c=1",
                chart if chart is not None else _chart("111", "recommended"),
            )
        )
        return 1

    return open_dialog


async def _run(page: _Page, opener, **kwargs):
    return await harvest_charts(
        page,
        open_dialog=opener,
        settle_ms=0,
        click_timeout_ms=100,
        tab_settle_ms=0,
        limit=kwargs.pop("limit", 16),
        **kwargs,
    )


# ── caption_refusal ─────────────────────────────────────────────────────


def test_a_tab_renamed_to_something_that_commits_is_refused():
    """Substring, not equality: 「立即发布」 contains 发布 and is exactly the
    control that must never be pressed."""
    assert caption_refusal("立即发布") is not None
    assert caption_refusal("发布") is not None
    assert caption_refusal("确定") is not None
    assert caption_refusal("删除草稿") is not None


def test_the_real_tab_captions_are_all_allowed():
    for caption in ("推荐", "热门榜", "收藏", "飙升榜", "原创榜", "卡点", "纯音乐", "DJ"):
        assert caption_refusal(caption) is None, caption


def test_a_caption_that_is_not_a_tab_shape_is_refused():
    assert caption_refusal("") is not None
    assert caption_refusal("   ") is not None
    assert caption_refusal("这是一段很长的文案不可能是一个标签页" * 2) is not None


# ── the flow ────────────────────────────────────────────────────────────


async def test_opening_the_panel_already_delivers_one_chart():
    """推荐 arrives without being asked for, so it is not clicked again."""
    page = _page(captions={"热门榜", "收藏"}, tab_payloads={})
    result = await _run(page, _opener(page))

    recommended = next(c for c in result.charts if c.category.key == OPENED_WITH_KEY)
    assert recommended.ok is True
    assert [s.music_id for s in recommended.songs] == ["111"]
    assert "推荐" not in page.clicks


async def test_each_remaining_tab_is_taken_and_lands_under_its_own_name():
    page = _page(
        captions={"热门榜", "收藏"},
        tab_payloads={"热门榜": _chart("222", "hot"), "收藏": _chart("333", "fav")},
    )
    result = await _run(page, _opener(page))

    by_name = {c.category.name: c for c in result.charts}
    assert [s.music_id for s in by_name["热门榜"].songs] == ["222"]
    assert [s.music_id for s in by_name["收藏"].songs] == ["333"]
    assert page.clicks == ["热门榜", "收藏"]


async def test_a_renamed_committing_tab_is_reported_and_never_clicked():
    """**The guard, asserted as a fact about the page.**

    Reporting the refusal is not enough on its own — an implementation that
    clicked first and refused afterwards would produce the same `ChartRead`.
    """
    categories = {
        "categories": [
            {"category_id": "1", "category_name": "推荐", "type": "recommend"},
            {"category_id": "9", "category_name": "立即发布", "type": "rank"},
        ],
        "status_code": 0,
    }

    async def open_dialog(target: _Page):
        target.emit(_Response(f"https://x{CATEGORY_URL_FRAGMENT}", categories))
        target.emit(_Response(f"https://x{LIST_URL_FRAGMENT}", _chart("111", "r")))
        return 1

    page = _page(captions={"立即发布"}, tab_payloads={"立即发布": _chart("666", "x")})
    result = await _run(page, open_dialog)

    refused = next(c for c in result.charts if c.category.name == "立即发布")
    assert refused.ok is False
    assert "refused" in refused.error
    assert page.clicks == []


async def test_a_tab_whose_click_failed_says_so_and_inherits_nothing():
    page = _page(
        captions={"热门榜", "收藏"},
        tab_payloads={"热门榜": _chart("222", "hot")},
    )
    page.click_raises.add("收藏")
    result = await _run(page, _opener(page))

    favourites = next(c for c in result.charts if c.category.name == "收藏")
    assert favourites.ok is False
    assert favourites.songs == ()
    assert "click failed" in favourites.error


async def test_a_tab_that_is_not_on_the_page_is_reported_not_skipped():
    page = _page(captions={"热门榜"}, tab_payloads={"热门榜": _chart("222", "hot")})
    result = await _run(page, _opener(page))

    favourites = next(c for c in result.charts if c.category.name == "收藏")
    assert favourites.ok is False
    assert "no node" in favourites.error


async def test_an_account_with_no_favourites_reads_as_an_empty_chart():
    page = _page(
        captions={"热门榜", "收藏"},
        tab_payloads={"热门榜": _chart("222", "hot"), "收藏": EMPTY_FAVOURITES},
    )
    result = await _run(page, _opener(page))

    favourites = next(c for c in result.charts if c.category.name == "收藏")
    assert favourites.ok is True
    assert favourites.songs == ()


async def test_a_panel_that_never_opened_is_a_typed_failure_not_an_empty_catalogue():
    page = _page(captions=set(), tab_payloads={})
    result = await _run(page, _opener(page, opens=False))

    assert result.charts == []
    assert result.categories == ()
    assert result.categories_error


async def test_a_panel_that_named_no_tabs_says_so_rather_than_reporting_none():
    """"We do not know what the charts are" must not overwrite a stored tab
    list with an empty one."""

    async def open_dialog(target: _Page):
        target.emit(_Response(f"https://x{LIST_URL_FRAGMENT}", _chart("111", "r")))
        return 1

    page = _page(captions=set(), tab_payloads={})
    result = await _run(page, open_dialog)

    assert result.categories_error
    # The chart the panel did deliver is still reported — a missing tab list is
    # not a reason to throw away data we actually read.
    assert [s.music_id for s in result.charts[0].songs] == ["111"]


async def test_the_cap_is_a_bound_on_interactions_not_a_silent_truncation():
    page = _page(
        captions={"热门榜", "收藏"},
        tab_payloads={"热门榜": _chart("222", "hot"), "收藏": _chart("333", "fav")},
    )
    result = await _run(page, _opener(page), limit=1)

    assert page.clicks == ["热门榜"]
    # Two charts: the one the panel opened with, plus the one tab the cap
    # allowed. 收藏 is absent rather than present-and-empty — the caller can
    # see it was never attempted from `charts_attempted`.
    assert [c.category.name for c in result.charts] == ["推荐", "热门榜"]


async def test_the_listener_is_detached_when_the_run_ends():
    page = _page(captions={"热门榜"}, tab_payloads={"热门榜": _chart("222", "hot")})
    await _run(page, _opener(page))
    assert page.handlers == []
