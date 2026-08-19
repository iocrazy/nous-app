"""Reading the 「选择音乐」 panel's chart tabs.

The fixtures are the 2026-08-19 capture
=======================================
Both payload shapes below were taken off a live creator account through the
recon path, with author strings replaced. Everything structural is as measured:

* `category_id` is a STRING, and 推荐 / 收藏 really do both answer `"1"` —
  they are told apart by `type` alone;
* on the list endpoint `music_id` arrives as a JSON **string** (the search
  endpoint is the one that ships a lossy numeric `id` beside an `id_str`);
* 收藏 on an account with no favourites answered with **no `songs` key at
  all**, `status_code: 0` — not an empty array.

That last one is why `payload_is_a_chart` exists, and per CLAUDE.md's 「边界
mock 必须用真实 JSON 形状」 a fixture that "helpfully" gave it `"songs": []`
would be testing a wire format the platform does not produce.
"""

from __future__ import annotations

from app.platforms.douyin_music_charts import (
    MusicCategory,
    MusicChartRecorder,
    build_chart,
    payload_is_a_chart,
    plan_tabs,
    read_categories,
    read_chart_songs,
    read_paging,
)

# The measured category payload, trimmed to the entries the tests need. The
# first three are copied verbatim, ids included, because the id collision
# between 推荐 and 收藏 is a fact about this platform that a made-up fixture
# would have tidied away.
CATEGORIES = {
    "categories": [
        {"category_id": "1", "category_name": "推荐", "type": "recommend"},
        {"category_id": "7088298745502646280", "category_name": "热门榜", "type": "rank"},
        {"category_id": "1", "category_name": "收藏", "type": "fav"},
        {"category_id": "7395823327471782694", "category_name": "卡点", "type": "category"},
    ],
    "extra": {"now": 1787124708000},
    "status_code": 0,
    "status_msg": "",
}


def _song(music_id: str, name: str, *, user_count: int | None = 8907493) -> dict:
    song = {
        "cover_url": "https://p26.douyinpic.com/aweme/100x100/ies-music/c.jpeg",
        "duration": 27,
        "music_author": "A",
        "music_id": music_id,
        "music_name": name,
        "play_url": "https://lf26-music-east.douyinstatic.com/obj/ies-music-hj/x.mp3",
    }
    if user_count is not None:
        song["user_count"] = user_count
    return song


def _chart(*songs: dict, cursor: str = "20", has_more: bool = True) -> dict:
    return {
        "cursor": cursor,
        "extra": {"now": 1787124708000},
        "has_more": has_more,
        "songs": list(songs),
        "status_code": 0,
        "status_msg": "",
    }


# The measured 收藏 response on an account with no favourites: 137 bytes, no
# `songs` key. Kept verbatim because the whole three-state contract turns on it.
EMPTY_FAVOURITES = {
    "extra": {"logid": "x", "now": 1787124708000},
    "status_code": 0,
    "status_msg": "",
}


# ── categories ──────────────────────────────────────────────────────────


def test_the_tab_list_is_read_in_the_order_the_panel_gave_it():
    tabs = read_categories(CATEGORIES)
    assert [t.name for t in tabs] == ["推荐", "热门榜", "收藏", "卡点"]
    assert [t.kind for t in tabs] == ["recommend", "rank", "fav", "category"]


def test_two_tabs_sharing_an_id_are_still_two_tabs():
    """**The collision that a store keyed on the id alone would merge.**

    推荐 and 收藏 both answer `category_id="1"` on the live panel. Delete
    `kind` from the key and this goes red — and in production one of the two
    charts would silently overwrite the other.
    """
    tabs = read_categories(CATEGORIES)
    recommend = next(t for t in tabs if t.name == "推荐")
    favourites = next(t for t in tabs if t.name == "收藏")
    assert recommend.category_id == favourites.category_id == "1"
    assert recommend.key != favourites.key
    assert len({t.key for t in tabs}) == 4


def test_a_tab_missing_its_type_is_dropped_rather_than_half_identified():
    tabs = read_categories(
        {"categories": [{"category_id": "1", "category_name": "推荐"}]}
    )
    assert tabs == []


def test_a_body_that_is_not_a_category_response_yields_nothing():
    assert read_categories({"status_code": 8}) == []
    assert read_categories(None) == []


# ── chart songs ─────────────────────────────────────────────────────────


def test_a_chart_yields_its_tracks_with_everything_a_picker_shows():
    songs = read_chart_songs(_chart(_song("7655518923810474790", "自带流量的音乐")))
    assert len(songs) == 1
    song = songs[0]
    assert song.music_id == "7655518923810474790"
    assert song.name == "自带流量的音乐"
    assert song.duration_s == 27
    assert song.user_count == 8907493
    assert song.play_url.endswith(".mp3")
    assert song.cover_url.startswith("https://")


def test_a_track_nobody_uses_and_a_track_that_did_not_say_are_different():
    """`0` is a real catalogue value — a track with exactly that produced the
    2026-08-17 production refusal. `None` means the payload was silent."""
    zero = read_chart_songs(_chart(_song("1", "a", user_count=0)))[0]
    silent = read_chart_songs(_chart(_song("2", "b", user_count=None)))[0]
    assert zero.user_count == 0
    assert silent.user_count is None


def test_the_id_is_kept_as_the_string_the_wire_carried():
    songs = read_chart_songs(_chart(_song("7655518923810474790", "x")))
    assert songs[0].music_id == "7655518923810474790"


def test_paging_is_carried_so_load_more_has_somewhere_to_start():
    assert read_paging(_chart(_song("1", "a"), cursor="20", has_more=True)) == ("20", True)
    assert read_paging(_chart(_song("1", "a"), cursor="", has_more=False)) == ("", False)
    assert read_paging(None) == ("", False)


# ── the three states ────────────────────────────────────────────────────


def test_an_account_with_no_favourites_is_an_empty_chart_not_a_failure():
    """**The measured 收藏 body.** No `songs` key, `status_code: 0`.

    Reporting this as a failed read would make an empty favourites list look
    broken forever; reporting a genuinely failed read as empty would ship a
    blank tab to the user as if the platform had nothing. Both directions are
    asserted, here and below.
    """
    chart = build_chart(MusicCategory("1", "收藏", "fav"), EMPTY_FAVOURITES)
    assert chart.ok is True
    assert chart.songs == ()
    assert chart.error == ""


def test_a_tab_that_answered_nothing_is_a_failed_read_not_an_empty_chart():
    chart = build_chart(MusicCategory("1", "收藏", "fav"), None)
    assert chart.ok is False
    assert chart.songs == ()
    assert chart.error


def test_a_body_that_is_not_a_chart_is_a_failed_read():
    chart = build_chart(MusicCategory("1", "推荐", "recommend"), {"status_code": 8})
    assert chart.ok is False


def test_the_chart_predicate_separates_empty_from_not_a_chart():
    assert payload_is_a_chart(EMPTY_FAVOURITES) is True
    assert payload_is_a_chart({"songs": []}) is True
    assert payload_is_a_chart({"status_code": 8}) is False
    assert payload_is_a_chart(None) is False


# ── planning which tabs to take ─────────────────────────────────────────


def test_the_tab_the_panel_already_answered_is_not_clicked_again():
    tabs = read_categories(CATEGORIES)
    plan = plan_tabs(tabs, opened_with="recommend:1")
    assert [t.name for t in plan] == ["热门榜", "收藏", "卡点"]


def test_the_cap_bounds_a_tab_row_that_grew():
    tabs = read_categories(CATEGORIES)
    plan = plan_tabs(tabs, opened_with="recommend:1", limit=2)
    assert [t.name for t in plan] == ["热门榜", "收藏"]


# ── the recorder ────────────────────────────────────────────────────────


class _Response:
    def __init__(self, url: str, body: bytes, status: int = 200) -> None:
        self.url = url
        self.status = status
        self._body = body

    async def body(self) -> bytes:
        return self._body


class _Page:
    def __init__(self) -> None:
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


def _json(payload: dict) -> bytes:
    import json as _j

    return _j.dumps(payload).encode()


CATEGORY_URL = "https://creator.douyin.com/web/api/media/music/category?aid=1128"
LIST_URL = "https://creator.douyin.com/web/api/media/music/list?category_id=1&type=rank"


async def test_each_tabs_list_lands_under_that_tab():
    page, rec = _Page(), MusicChartRecorder()
    rec.attach(page)
    rec.phase("rank:7088298745502646280")
    page.emit(_Response(LIST_URL, _json(_chart(_song("111", "hot")))))
    rec.phase("rank:6854399861215747336")
    page.emit(_Response(LIST_URL, _json(_chart(_song("222", "original")))))
    await rec.settle()

    hot = read_chart_songs(rec.list_payload("rank:7088298745502646280"))
    original = read_chart_songs(rec.list_payload("rank:6854399861215747336"))
    assert [s.music_id for s in hot] == ["111"]
    assert [s.music_id for s in original] == ["222"]


async def test_a_tab_that_fired_nothing_stays_empty_rather_than_inheriting():
    """**The single most plausible way this harvest could lie.**

    If attribution were by order rather than by phase, a tab whose request
    never fired would pick up the previous tab's list and we would publish one
    chart's songs under another chart's name — with every count looking
    healthy.
    """
    page, rec = _Page(), MusicChartRecorder()
    rec.attach(page)
    rec.phase("rank:hot")
    page.emit(_Response(LIST_URL, _json(_chart(_song("111", "hot")))))
    rec.phase("fav:1")  # this tab fires nothing at all
    await rec.settle()

    assert rec.list_payload("fav:1") is None
    assert build_chart(MusicCategory("1", "收藏", "fav"), rec.list_payload("fav:1")).ok is False


async def test_the_category_response_is_kept_apart_from_the_lists():
    page, rec = _Page(), MusicChartRecorder()
    rec.attach(page)
    rec.phase("open")
    page.emit(_Response(CATEGORY_URL, _json(CATEGORIES)))
    page.emit(_Response(LIST_URL, _json(_chart(_song("111", "a")))))
    await rec.settle()

    assert [t.name for t in read_categories(rec.category_payload)] == [
        "推荐",
        "热门榜",
        "收藏",
        "卡点",
    ]
    assert [s.music_id for s in read_chart_songs(rec.list_payload("open"))] == ["111"]


async def test_unrelated_traffic_is_ignored():
    page, rec = _Page(), MusicChartRecorder()
    rec.attach(page)
    rec.phase("open")
    page.emit(
        _Response(
            "https://tsearch.amemv.com/openapi/aweme/v1/music/search/?keyword=x",
            _json({"music": [{"id_str": "9", "title": "t"}]}),
        )
    )
    await rec.settle()
    assert rec.list_payload("open") is None
    assert rec.category_payload is None


async def test_a_non_200_is_not_read_as_an_empty_chart():
    page, rec = _Page(), MusicChartRecorder()
    rec.attach(page)
    rec.phase("open")
    page.emit(_Response(LIST_URL, b"", status=403))
    await rec.settle()
    assert build_chart(MusicCategory("1", "推荐", "recommend"), rec.list_payload("open")).ok is (
        False
    )
