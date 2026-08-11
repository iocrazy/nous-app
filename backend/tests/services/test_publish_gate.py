"""提交时那道门（图集设计 §2 D3 校验前移）。

这里全是纯函数测试：``publish_request_problems`` 不碰 DB、不碰浏览器。
router 层"被拒的批次不建任何行"的证据在 ``tests/api/test_distribution_publish_api.py``。
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.distribution_publish import PublishTaskCreate
from app.services.distribution.publish_gate import (
    REASON_ACCOUNT_NOT_SESSION_BOUND,
    REASON_PUBLISHING_NOT_IMPLEMENTED,
    publish_request_problems,
    resolves_to_session,
)
from app.services.distribution.publish_options import (
    SCHEDULE_TOO_SOON,
    SELF_DECLARATION_AI,
)
from app.services.distribution.session_adapter import (
    SESSION_PLATFORM_PROFILES,
    SHAPE_COVER_NOT_SUPPORTED_FOR_IMAGES,
    SHAPE_DECLARATION_UNSUPPORTED,
    SHAPE_INVALID_SCHEDULE,
    SHAPE_TITLE_TOO_LONG,
    SHAPE_TOO_FEW_IMAGES,
    SHAPE_TOO_MANY_IMAGES,
    SHAPE_UNSUPPORTED_CONTENT_TYPE,
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)

SESSION_ACCOUNT = {"id": "900", "platform": "douyin", "auth_type": "session"}
OAUTH_ACCOUNT = {"id": "901", "platform": "douyin", "auth_type": "oauth"}


@pytest.fixture
def images_enabled(monkeypatch):
    """让 douyin 暂时声明支持 images。

    生产声明仍是 ``{"video"}``（T7 才翻转，且必须与 browser 侧同一个 PR），
    所以图集的每一条形状规则在此刻的生产里都够不着 —— 测试直接把画像翻过来
    验，正是为了让这些规则在翻转那天之前就已经被验证过。
    """
    profile = SESSION_PLATFORM_PROFILES["douyin"]
    monkeypatch.setitem(
        SESSION_PLATFORM_PROFILES,
        "douyin",
        replace(profile, content_types=frozenset({"video", "images"})),
    )
    return SESSION_PLATFORM_PROFILES["douyin"]


def _body(**over) -> PublishTaskCreate:
    base = dict(
        title="Summer Trip",
        content_type="video",
        resource_ids=["30"],
        account_ids=["900"],
        channel="session",
    )
    base.update(over)
    return PublishTaskCreate(**base)  # type: ignore[arg-type]


def _images(count: int, **over) -> PublishTaskCreate:
    return _body(
        content_type="images",
        resource_ids=[str(i) for i in range(count)],
        **over,
    )


def _reasons(problems):
    return [p.reason for p in problems]


# ── 实测值真的落进了画像与能力下发 ────────────────────────────


def test_douyin_profile_carries_the_measured_image_bounds():
    """[实测 2026-08-11]（§3.4 V2）上限 35、下限 1。

    这条断言的意义不是"35 是对的"，而是"这两个数被明确写下来过" ——
    ``max_images`` 曾经是 None（"待实测填入"），下发给前端的能力里就是 null，
    于是前端只能自己猜一个上限或者干脆不拦。
    """
    profile = SESSION_PLATFORM_PROFILES["douyin"]
    assert profile.max_images == 35
    assert profile.min_images == 1


def test_capabilities_endpoint_projects_the_measured_bounds():
    from app.services.distribution.session_adapter import platform_capabilities

    douyin = platform_capabilities()["douyin"]
    assert (douyin.min_images, douyin.max_images) == (1, 35)


# ── 张数上下界 ────────────────────────────────────────────────


def test_too_many_images_is_rejected_at_submit(images_enabled):
    profile = replace(images_enabled, max_images=3)
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(SESSION_PLATFORM_PROFILES, "douyin", profile)
        problems = publish_request_problems(_images(5), [SESSION_ACCOUNT], now=NOW)
    assert _reasons(problems) == [SHAPE_TOO_MANY_IMAGES]
    assert "5 > 3" in problems[0].message
    assert problems[0].account_id == "900"


def test_min_images_two_rejects_a_single_image_and_one_lets_it_through(
    images_enabled,
):
    """反向验证 2：把下界设成 2 必须真的拒掉 1 张，设回 1 必须放行。

    下界如果是装饰品（读了但没参与判定），这两个断言里第一个就会红。
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(
            SESSION_PLATFORM_PROFILES, "douyin", replace(images_enabled, min_images=2)
        )
        strict = publish_request_problems(_images(1), [SESSION_ACCOUNT], now=NOW)
    assert _reasons(strict) == [SHAPE_TOO_FEW_IMAGES]

    # min_images 已经是实测的 1 —— 无需再改画像，直接用生产值验放行。
    assert publish_request_problems(_images(1), [SESSION_ACCOUNT], now=NOW) == []


def test_min_images_never_applies_to_a_video_batch():
    """视频批次的 image_count 恒为 0，拿下界去卡它会把每一条视频都拒掉。"""
    assert publish_request_problems(_body(), [SESSION_ACCOUNT], now=NOW) == []


def test_exactly_at_the_measured_ceiling_is_allowed(images_enabled):
    """35 是闭区间上界。差一个的错误在这里最难发现，也最烦人。"""
    assert publish_request_problems(_images(35), [SESSION_ACCOUNT], now=NOW) == []


# ── 混选 session / 非 session 账号（D3 的核心动机） ────────────


def test_images_with_a_non_session_account_is_rejected_whole_batch(images_enabled):
    """§1.2 的隐患：``decide_channel`` 会把 OAuth 账号降级到 h5，于是一个图集
    批次一半走浏览器一半走 H5 分享交接。提交时就拒掉，不产生半成品批次。"""
    problems = publish_request_problems(
        _images(3, account_ids=["900", "901"]),
        [SESSION_ACCOUNT, OAUTH_ACCOUNT],
        now=NOW,
    )
    assert _reasons(problems) == [REASON_ACCOUNT_NOT_SESSION_BOUND]
    # 用户最需要知道的是"是哪个账号"。
    assert problems[0].account_id == "901"


def test_video_with_a_non_session_account_still_goes_through():
    """视频的 session→h5 降级是既有的、被明确设计过的行为
    （``decide_channel`` 的 docstring）。本次只拒图集的那一种 —— 顺手把视频
    也拒了，就是拿一个没人要求的行为变更去换一个漂亮的规则。"""
    assert (
        publish_request_problems(
            _body(account_ids=["900", "901"]),
            [SESSION_ACCOUNT, OAUTH_ACCOUNT],
            now=NOW,
        )
        == []
    )


def test_h5_batch_is_not_measured_against_the_session_profile():
    """channel=h5 的批次走的是另一条通道，profile 描述的是会话通道 ——
    别拿一张画像去判它没画的东西。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(
            SESSION_PLATFORM_PROFILES,
            "douyin",
            replace(SESSION_PLATFORM_PROFILES["douyin"], max_title_len=20),
        )
        # 同一个标题走 session 通道会被拒（见 test_per_account_title_override…），
        # 走 h5 则与这张画像无关。
        assert (
            publish_request_problems(
                _body(channel="h5", title="x" * 50), [OAUTH_ACCOUNT], now=NOW
            )
            == []
        )


@pytest.mark.parametrize(
    "channel,auth_type",
    [
        ("session", "session"),
        ("session", "oauth"),
        ("session", None),
        ("h5", "session"),
        ("h5", "oauth"),
        ("official", "session"),
        ("official", "oauth"),
    ],
)
def test_resolves_to_session_never_drifts_from_decide_channel(channel, auth_type):
    """守卫：``publish_gate.resolves_to_session`` 是 ``decide_channel`` 第一个
    分支的同义改写。两者漂移的后果是提交时按一条规则拒、执行时按另一条跑 ——
    正是把规则抄一份会犯的错。所以由测试盯着，不靠注释里的自觉。"""
    from app.workflows.publish_distribution import decide_channel

    account = {"auth_type": auth_type} if auth_type else {}
    assert resolves_to_session(channel, account) == (
        decide_channel(channel, account) == "session"
    )


# ── 平台能力与形状 ────────────────────────────────────────────


def test_images_are_rejected_while_the_platform_still_declares_video_only():
    """生产此刻的声明是 ``{"video"}``（T7 才翻转）—— 图集请求必须被拒，
    且理由是"这个平台不支持"，不是别的什么。"""
    problems = publish_request_problems(_images(3), [SESSION_ACCOUNT], now=NOW)
    assert _reasons(problems) == [SHAPE_UNSUPPORTED_CONTENT_TYPE]


def test_platform_without_a_publisher_is_rejected_at_submit():
    """小红书能绑账号但发不了。走到 workflow 也必然是一行 failed，
    没有任何不确定性 —— 那就在提交时说清楚。"""
    account = {"id": "902", "platform": "xiaohongshu", "auth_type": "session"}
    problems = publish_request_problems(_body(account_ids=["902"]), [account], now=NOW)
    assert _reasons(problems) == [REASON_PUBLISHING_NOT_IMPLEMENTED]


def test_cover_asset_on_an_image_post_is_rejected(images_enabled):
    """D4：图文页确实有封面控件，但 [实测 2026-08-11]（§3.4 V8）它是**从已上传
    的图片里挑一张**，不是视频那种独立上传的第五个文件。带 cover 素材的图集
    请求是语义错误，不是可以忽略的多余字段。"""
    problems = publish_request_problems(
        _images(3, cover_vertical_resource_id="77"), [SESSION_ACCOUNT], now=NOW
    )
    assert _reasons(problems) == [SHAPE_COVER_NOT_SUPPORTED_FOR_IMAGES]


def test_video_keeps_its_cover():
    assert (
        publish_request_problems(
            _body(cover_vertical_resource_id="77"), [SESSION_ACCOUNT], now=NOW
        )
        == []
    )


def test_schedule_window_is_checked_at_submit_too():
    """请求 schema 那道用的是真实时钟，所以这里的定时时间必须是真的合法；
    ``now`` 往后拨到只剩 2 小时 5 分，模拟"排队期间滑进窗口内"。"""
    sched = datetime.now(timezone.utc) + timedelta(days=1)
    problems = publish_request_problems(
        _body(scheduled_at=sched),
        [SESSION_ACCOUNT],
        now=sched - timedelta(hours=2, minutes=5),
    )
    assert _reasons(problems) == [SHAPE_INVALID_SCHEDULE]
    assert problems[0].message == SCHEDULE_TOO_SOON


def test_per_account_title_override_is_what_gets_measured():
    """批次标题合法、账号级覆盖超长 —— 校验批次那一份等于在最容易出错的
    地方漏判（workflow 用的正是覆盖后的标题）。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(
            SESSION_PLATFORM_PROFILES,
            "douyin",
            replace(SESSION_PLATFORM_PROFILES["douyin"], max_title_len=20),
        )
        problems = publish_request_problems(
            _body(account_configs={"900": {"title": "x" * 50}}),
            [SESSION_ACCOUNT],
            now=NOW,
        )
    assert _reasons(problems) == [SHAPE_TITLE_TOO_LONG]


def test_ai_content_is_resolved_into_a_declaration_before_the_check():
    """``ai_content=True`` 会被 ``resolve_self_declaration`` 补成
    ``内容由AI生成``（workflow 组装 intent 时就是这么做的）。这道门若只读
    ``self_declaration`` 列，两道门看到的就不是同一个请求。"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(
            SESSION_PLATFORM_PROFILES,
            "douyin",
            replace(SESSION_PLATFORM_PROFILES["douyin"], self_declarations=frozenset()),
        )
        problems = publish_request_problems(
            _body(ai_content=True), [SESSION_ACCOUNT], now=NOW
        )
    assert _reasons(problems) == [SHAPE_DECLARATION_UNSUPPORTED]


def test_an_explicit_declaration_still_wins_over_ai_content():
    """同一份 ``resolve_self_declaration`` 语义：显式选的那个胜出。"""
    assert (
        publish_request_problems(
            _body(ai_content=True, self_declaration=SELF_DECLARATION_AI),
            [SESSION_ACCOUNT],
            now=NOW,
        )
        == []
    )


def test_an_account_row_without_a_platform_is_skipped_not_guessed():
    """画像查不到就跳过平台相关的判定 —— 猜一个 douyin 去校验，等于让这道门
    对一个它不认识的平台下结论。"""
    assert (
        publish_request_problems(
            _body(), [{"id": "900", "auth_type": "session"}], now=NOW
        )
        == []
    )
