"""平台曲库搜索：那些"200 但其实失败了"的形态，一个都不许静默。

这条链的每一步都有一种"看起来成功"的失败形态，而它们的共同表现是**一个空列表**：

* creator 站对不存在的路由回 ``status_code:1``、对未登录回 ``status_code:8``
  —— **两者都是 HTTP 200**；
* 搜索的 ``aid`` 传成 creator 站的 2906（而不是 App 的 1128）→ 200，
  ``music:[]``，``search_nil_item="invalid_app"``；
* ``/music/list`` 接口把 keyword 参数**静默忽略**，回的是该 tab 的常规列表且
  ``status_code:0`` —— 最容易被误当成"搜到了这些"的一种。

所以本文件的每一条都在问同一个问题：**这个失败会不会变成一个空列表**。

fixture 的形状是真的
====================
下面的 ``SEARCH_BODY`` 抄的是 2026-08-15 实测响应（容器内 `docker exec`，
cookie 未离开进程）。三处刻意保留了"不像 TypeScript 会写的"形状，因为它们就是
真的（CLAUDE.md「边界 mock 必须用真实 JSON 形状」）：

* ``id`` 是 **JSON number** 且超过 2^53，``id_str`` 是字符串，两者并存；
* ``has_more`` 是 **1/0 整数**，不是 bool；
* ``duration`` 是**秒**的整数（实测同一次搜索回 49 / 221 / 323 / 267）。
  这一维单独实测过：它是发布时三元组指纹的一维，单位搞错的表现不是报错，是
  每一次选择都变成"发布时找不到那首歌"。

歌名与作者是平台公开曲库的内容，不是用户数据。
"""

from __future__ import annotations

import pytest

from app.services.distribution import music_catalog as mc

pytestmark = pytest.mark.unit


def _cover(url: str) -> dict:
    return {"uri": "tos-cn/x", "url_list": [url], "width": 720, "height": 720}


SEARCH_BODY = {
    "status_code": 0,
    "cursor": 5,
    "has_more": 1,
    "music": [
        {
            "id": 6953836671917951012,
            "id_str": "6953836671917951012",
            "title": "起风了",
            "author": "叶龙发",
            "duration": 49,
            "user_count": 30025,
            "cover_medium": _cover("https://p3.example.invalid/m.jpeg"),
            "play_url": {"url_list": ["https://sf.example.invalid/a.mp3"]},
            "is_commerce_music": True,
        },
        {
            "id": 7673728791198320674,
            "id_str": "7673728791198320674",
            "title": "起风了",
            "author": "安禾同学",
            "duration": 221,
            "user_count": 9,
            "cover_medium": _cover("https://p3.example.invalid/n.jpeg"),
            "play_url": {"url_list": ["https://sf.example.invalid/b.mp3"]},
        },
    ],
}


@pytest.fixture(autouse=True)
def clean_cache():
    mc.clear_catalog_cache()
    yield
    mc.clear_catalog_cache()


# ── 解析：真实形状进，我们的形状出 ────────────────────────────


def test_the_string_id_is_the_one_that_is_kept():
    """上游同一条结果同时给 ``id``（number，超过 2^53）与 ``id_str``。取前者的
    话，这个数一旦经 JSON 到前端就已经不是它了 —— 而它是"发出去的是哪一首"的
    唯一凭据。"""
    tracks, _, _ = mc.parse_search_payload(SEARCH_BODY)
    assert tracks[0].music_id == "6953836671917951012"
    assert isinstance(tracks[0].music_id, str)


def test_a_row_that_only_has_the_numeric_id_is_not_usable_as_an_identity():
    """**守卫**，也是上一条无法单独提供的那一半：在 Python 里
    ``str(6953836671917951012) == "6953836671917951012"``，所以"读的是哪个字段"
    在一条正常结果上**看不出差别**。

    这一条能看出来：只有 ``id`` 没有 ``id_str`` 的行必须被丢掉。把解析改成
    读 ``id``，这行就会被留下 —— 于是一个到了浏览器就已经失真的数字被当成了
    身份。
    """
    body = {
        "status_code": 0,
        "music": [{"id": 6953836671917951012, "title": "起风了", "duration": 49}],
    }
    tracks, _, _ = mc.parse_search_payload(body)
    assert tracks == []


def test_durations_are_seconds_and_survive_as_they_came():
    """[实测 2026-08-15] 49 / 221 / 323 / 267 —— 只可能是秒。若上游哪天改成毫秒，
    三元组的时长那一维会全线对不上，表现是"每次选歌都发布失败"，所以这个单位
    值得一条测试钉住。"""
    tracks, _, _ = mc.parse_search_payload(SEARCH_BODY)
    assert [t.duration for t in tracks] == [49, 221]


def test_the_integer_has_more_is_read_as_a_boolean():
    """上游给的是 1/0 整数，不是 JSON bool。照抄真实形状。"""
    tracks, cursor, has_more = mc.parse_search_payload(SEARCH_BODY)
    assert (cursor, has_more) == (5, True)
    assert len(tracks) == 2


def test_no_more_pages_is_read_as_such():
    _, _, has_more = mc.parse_search_payload({**SEARCH_BODY, "has_more": 0})
    assert has_more is False


def test_the_display_fields_the_picker_needs_all_come_through():
    """封面/歌名/作者/时长/使用量 —— 用户挑一张卡片正是靠这五样。缺一样，
    选择器就退化回"让我打歌名"。"""
    track = mc.parse_search_payload(SEARCH_BODY)[0][0]
    assert track.title == "起风了"
    assert track.author == "叶龙发"
    assert track.user_count == 30025
    assert track.cover_url == "https://p3.example.invalid/m.jpeg"
    assert track.play_url == "https://sf.example.invalid/a.mp3"


def test_a_track_with_no_cover_is_still_a_track():
    """缺封面是常态。丢掉整条结果，用户就会看到一个"平台没有这首歌"的假象。"""
    body = {**SEARCH_BODY, "music": [{"id_str": "1", "title": "T", "duration": 10}]}
    tracks, _, _ = mc.parse_search_payload(body)
    assert (tracks[0].cover_url, tracks[0].play_url) == ("", "")


def test_a_result_without_an_id_is_dropped_rather_than_shown_unpickable():
    """没有 ``id_str`` 的行成不了身份。显示它等于给用户一张点了会发错歌的卡片。"""
    body = {**SEARCH_BODY, "music": [{"title": "No Id", "duration": 10}]}
    tracks, _, _ = mc.parse_search_payload(body)
    assert tracks == []


# ── 那些 HTTP 200 的失败 ──────────────────────────────────────


@pytest.mark.parametrize(
    "status,label",
    [
        (1, "route gone"),
        (8, "not logged in"),
        (4, "bad parameter"),
    ],
)
def test_an_upstream_business_error_is_refused_even_when_a_list_came_with_it(
    status, label
):
    """**守卫**。三种都是 **HTTP 200**。把 200 当成功，它们会一起变成一个面板上
    的列表 —— 用户读作"平台的答案"，而真相是路由没了 / 会话掉了 / 参数错了。

    ⚠️ 这里**故意带上一份 music 列表**：非 0 状态时上游往往仍给一份列表（典型
    是风控要求验证），而那是一份不该信的列表。不带列表的版本证明不了这道门 ——
    删掉 status_code 判断后它也会因为"没有 music 键"而红，测试就变成了在为另一
    个理由通过。断言里连业务码一起比，是同一个理由。
    """
    body = {**SEARCH_BODY, "status_code": status, "status_msg": "…"}
    with pytest.raises(mc.MusicCatalogError) as excinfo:
        mc.parse_search_payload(body)
    assert excinfo.value.reason == mc.REASON_UPSTREAM_SHAPE
    assert str(status) in excinfo.value.message


def test_a_body_without_the_music_key_is_a_shape_failure_not_a_result():
    """上游哪天改字段名（比如把 ``music`` 改成 ``music_list``），这道门必须响。
    悄悄回空列表 = 功能静默死掉，而所有探针都绿。"""
    with pytest.raises(mc.MusicCatalogError) as excinfo:
        mc.parse_search_payload({"status_code": 0, "cursor": 0})
    assert excinfo.value.reason == mc.REASON_UPSTREAM_SHAPE


def test_an_empty_result_list_is_a_legitimate_success():
    """空列表**只**在上游真的回了空的时候出现。它是成功路径。"""
    tracks, cursor, has_more = mc.parse_search_payload(
        {"status_code": 0, "music": [], "cursor": 0, "has_more": 0}
    )
    assert (tracks, cursor, has_more) == ([], 0, False)


def test_a_signature_response_without_a_signature_is_typed():
    with pytest.raises(mc.MusicCatalogError) as excinfo:
        mc.parse_signature({"status_code": 0})
    assert excinfo.value.reason == mc.REASON_SIGNATURE_UNAVAILABLE


def test_an_unauthenticated_signature_call_is_refused_even_if_it_carries_a_signature():
    """``status_code:8`` = 未登录，HTTP 仍是 200。同上：**故意**给一个签名字段，
    否则删掉状态判断后这条会因为"没有 signature"而红，那是另一个理由。"""
    with pytest.raises(mc.MusicCatalogError) as excinfo:
        mc.parse_signature(
            {"status_code": 8, "status_msg": "用户未登录", "signature": "s" * 121}
        )
    assert excinfo.value.reason == mc.REASON_UPSTREAM_SHAPE


# ── 请求本身不成立 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_empty_keyword_never_reaches_the_platform():
    with pytest.raises(mc.MusicCatalogError) as excinfo:
        await mc.search_music(platform="douyin", keyword="   ", account_id=1)
    assert excinfo.value.reason == mc.REASON_KEYWORD_EMPTY
    assert excinfo.value.http_status == 400


@pytest.mark.asyncio
async def test_a_platform_with_no_catalogue_says_so_instead_of_returning_nothing():
    """ "我们根本没问"不能长得像"这个平台没有这首歌"。"""
    with pytest.raises(mc.MusicCatalogError) as excinfo:
        await mc.search_music(platform="xiaohongshu", keyword="起风了", account_id=1)
    assert excinfo.value.reason == mc.REASON_PLATFORM_UNSUPPORTED
    assert excinfo.value.http_status == 400


# ── 出网与风控：带身份的请求要少 ──────────────────────────────


@pytest.fixture
def fake_upstream(monkeypatch):
    """记录每一次出网调用，并按 URL 给出可编排的响应。"""
    calls: list[tuple[str, dict, dict]] = []
    script: dict[str, list] = {}

    async def _get_json(url, *, params, headers):
        calls.append((url, dict(params), dict(headers)))
        queue = script.get(url)
        if not queue:
            raise AssertionError(f"unscripted call to {url}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(mc, "_get_json", _get_json)
    monkeypatch.setattr(
        mc, "_account_cookie_header", lambda account_id: _cookie(account_id)
    )
    return calls, script


async def _cookie(_account_id):
    return "sessionid=REDACTED"


AUTH_OK = {"status_code": 0, "signature": "s" * 121}


@pytest.mark.asyncio
async def test_a_search_carries_the_app_aid_not_the_creator_one(fake_upstream):
    """**守卫**。传 creator 站的 2906 上游会 200 且回空列表（实测
    ``search_nil_item="invalid_app"``）—— 一个不报错的死胡同，长得跟"这首歌不
    存在"一模一样。"""
    calls, script = fake_upstream
    script[mc.DOUYIN_AUTH_URL] = [AUTH_OK]
    script[mc.DOUYIN_SEARCH_URL] = [SEARCH_BODY]

    await mc.search_music(platform="douyin", keyword="起风了", account_id=7)

    search_params = [p for (url, p, _) in calls if url == mc.DOUYIN_SEARCH_URL][0]
    assert search_params["aid"] == "1128"
    auth_params = [p for (url, p, _) in calls if url == mc.DOUYIN_AUTH_URL][0]
    assert auth_params["aid"] == "2906"


@pytest.mark.asyncio
async def test_the_search_carries_the_signature_header(fake_upstream):
    calls, script = fake_upstream
    script[mc.DOUYIN_AUTH_URL] = [AUTH_OK]
    script[mc.DOUYIN_SEARCH_URL] = [SEARCH_BODY]

    await mc.search_music(platform="douyin", keyword="起风了", account_id=7)

    headers = [h for (url, _, h) in calls if url == mc.DOUYIN_SEARCH_URL][0]
    assert headers["Agw-Auth"] == AUTH_OK["signature"]
    # 搜索本身不带 cookie（上游前端就是 withCredentials:false）。带上等于把会话
    # 送到一个不需要它的第三方域。
    assert "cookie" not in {k.lower() for k in headers}


@pytest.mark.asyncio
async def test_twenty_searches_cost_one_request_with_the_users_identity(fake_upstream):
    """**守卫**。换签名那一步是这条链上**唯一**带账号 cookie 的请求，每一次都
    算进那个账号的风控画像。把签名缓存删掉，这条就红 —— 而红之前，一个打字的
    用户就是一串带身份的请求。"""
    calls, script = fake_upstream
    script[mc.DOUYIN_AUTH_URL] = [AUTH_OK]
    script[mc.DOUYIN_SEARCH_URL] = [SEARCH_BODY for _ in range(20)]

    for i in range(20):
        # 每次换个词，绕开搜索结果缓存 —— 这条量的是签名，不是结果缓存。
        await mc.search_music(platform="douyin", keyword=f"song {i}", account_id=7)

    assert sum(1 for (url, _, _) in calls if url == mc.DOUYIN_AUTH_URL) == 1
    assert sum(1 for (url, _, _) in calls if url == mc.DOUYIN_SEARCH_URL) == 20


@pytest.mark.asyncio
async def test_the_same_word_twice_does_not_ask_the_platform_twice(fake_upstream):
    calls, script = fake_upstream
    script[mc.DOUYIN_AUTH_URL] = [AUTH_OK]
    script[mc.DOUYIN_SEARCH_URL] = [SEARCH_BODY]

    first = await mc.search_music(platform="douyin", keyword="起风了", account_id=7)
    second = await mc.search_music(platform="douyin", keyword="起风了", account_id=7)

    assert (first.cached, second.cached) == (False, True)
    assert sum(1 for (url, _, _) in calls if url == mc.DOUYIN_SEARCH_URL) == 1
    assert [t.music_id for t in second.tracks] == [t.music_id for t in first.tracks]


@pytest.mark.asyncio
async def test_a_failure_is_never_cached_as_a_result(fake_upstream):
    """缓存一次失败 = 把一次抖动放大成 5 分钟的功能不可用。"""
    calls, script = fake_upstream
    script[mc.DOUYIN_AUTH_URL] = [AUTH_OK]
    script[mc.DOUYIN_SEARCH_URL] = [
        {"status_code": 8, "status_msg": "用户未登录"},
        SEARCH_BODY,
    ]

    with pytest.raises(mc.MusicCatalogError):
        await mc.search_music(platform="douyin", keyword="起风了", account_id=7)
    ok = await mc.search_music(platform="douyin", keyword="起风了", account_id=7)

    assert ok.cached is False
    assert len(ok.tracks) == 2


@pytest.mark.asyncio
async def test_an_expired_signature_is_reminted_once_and_the_search_retried(
    fake_upstream,
):
    """签名的真实 TTL 未实测，所以"被拒了就作废重取一次"是唯一可靠的假设。
    **只重试一次** —— 用户在等，指数退避在这里是错的形状。"""
    calls, script = fake_upstream
    script[mc.DOUYIN_AUTH_URL] = [AUTH_OK, {"status_code": 0, "signature": "fresh"}]
    script[mc.DOUYIN_SEARCH_URL] = [
        mc.MusicCatalogError(mc.REASON_UPSTREAM_STATUS, "upstream returned HTTP 403"),
        SEARCH_BODY,
    ]

    result = await mc.search_music(platform="douyin", keyword="起风了", account_id=7)

    assert len(result.tracks) == 2
    assert sum(1 for (url, _, _) in calls if url == mc.DOUYIN_AUTH_URL) == 2
    assert [h for (url, _, h) in calls if url == mc.DOUYIN_SEARCH_URL][-1][
        "Agw-Auth"
    ] == "fresh"


@pytest.mark.asyncio
async def test_a_broken_authorisation_backs_off_instead_of_hammering(fake_upstream):
    """**守卫**。鉴权坏掉时，一个打字的用户会把它变成一串**带账号身份**的请求。
    这是刻意的一次"缓存失败"，与 ``topic_suggest`` 的"只缓存成功"相反 ——
    代价不对称：那边多打几次只是浪费，这边是拿用户的账号去撞风控。

    删掉退避这条就红（第二次会再打一次 auth）。
    """
    calls, script = fake_upstream
    script[mc.DOUYIN_AUTH_URL] = [{"status_code": 8, "status_msg": "用户未登录"}]

    with pytest.raises(mc.MusicCatalogError):
        await mc.search_music(platform="douyin", keyword="a", account_id=7)
    with pytest.raises(mc.MusicCatalogError) as second:
        await mc.search_music(platform="douyin", keyword="b", account_id=7)

    assert sum(1 for (url, _, _) in calls if url == mc.DOUYIN_AUTH_URL) == 1
    assert second.value.reason == mc.REASON_SIGNATURE_UNAVAILABLE
    # 退避与"坏了"要能说成两句话：前者稍后会自己好。
    assert second.value.retry_after_s is not None
