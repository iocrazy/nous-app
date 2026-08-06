"""发布表单的三个平台原生字段：自主声明 / 定时窗口 / 合集。

这些字段抖音发布页上有、我们此前没有，补齐时最容易出的两类错各有一条守卫：

1. **窗口边界算错** —— 平台只收「2 小时后 ~ 14 天内」，而我们对外的下限还要
   再加 10 分钟上传余量（见 ``SCHEDULE_LEAD_SLACK``）。差几分钟的判断错误
   不会在开发时暴露（谁会挑 1h59m 去点一次发布），只会在真实用户排一批定时
   任务时被平台整批拒掉。所以每个边界逐个钉住。
2. **自主声明取值漂** —— 值是抖音页面上的六个**原文**，浏览器侧靠它做 DOM
   文本匹配。写错一个字的后果不是报错，而是"选项没选中但作品照发"，一个静默
   的合规缺口。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.distribution_publish import PublishTaskCreate
from app.services.distribution.publish_options import (
    SCHEDULE_LEAD_SLACK,
    SCHEDULE_MIN_LEAD,
    SCHEDULE_NAIVE,
    SCHEDULE_PLATFORM_MIN_LEAD,
    SCHEDULE_TOO_FAR,
    SCHEDULE_TOO_SOON,
    SELF_DECLARATION_AI,
    SELF_DECLARATION_FICTION,
    SELF_DECLARATION_NONE,
    SELF_DECLARATION_REPOST,
    SELF_DECLARATIONS,
    normalize_collection,
    resolve_self_declaration,
    self_declaration_conflicts,
    validate_scheduled_at,
)
from app.services.distribution.session_adapter import (
    PublishIntent,
    PublishMedia,
    SessionAdapter,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


# ── 定时窗口（2h ~ 14d） ────────────────────────────────────────


@pytest.mark.parametrize(
    "delta,expected",
    [
        (timedelta(hours=1, minutes=59), SCHEDULE_TOO_SOON),
        # 平台底线是 2h，但生效下限是 2h10m —— 定时时间在**上传完成之后**才填进
        # 创作页，2h00~2h10 这一段会过我们所有校验然后被抖音自己拒掉。浏览器侧
        # 用的是同一个数（跨服务约定），两侧不一致就会出现"UI 放行、浏览器拒绝"。
        (timedelta(hours=2), SCHEDULE_TOO_SOON),
        (timedelta(hours=2, minutes=1), SCHEDULE_TOO_SOON),
        (timedelta(hours=2, minutes=10), None),  # 闭区间下界
        (timedelta(hours=2, minutes=11), None),
        (timedelta(days=13), None),
        (timedelta(days=14), None),  # 闭区间上界（上限不加余量）
        (timedelta(days=14, minutes=1), SCHEDULE_TOO_FAR),
        (timedelta(0), SCHEDULE_TOO_SOON),
        (timedelta(hours=-1), SCHEDULE_TOO_SOON),  # 过去的时间
    ],
)
def test_schedule_window_boundaries(delta, expected):
    assert validate_scheduled_at(NOW + delta, now=NOW) == expected


def test_effective_lower_bound_is_the_platform_minimum_plus_upload_slack():
    """两个常量的关系写死在测试里：改了平台底线或余量，这条会提醒你两侧要一起改。"""
    assert SCHEDULE_MIN_LEAD == SCHEDULE_PLATFORM_MIN_LEAD + SCHEDULE_LEAD_SLACK
    assert SCHEDULE_PLATFORM_MIN_LEAD == timedelta(hours=2)
    assert SCHEDULE_LEAD_SLACK == timedelta(minutes=10)


def test_none_schedule_is_immediate_publish_not_an_error():
    assert validate_scheduled_at(None, now=NOW) is None


def test_naive_datetime_is_rejected_never_assumed_utc():
    """时区猜错 = 帖子提前或推迟 8 小时发出去，且不可撤销。宁可拒。"""
    naive = (NOW + timedelta(days=1)).replace(tzinfo=None)
    assert validate_scheduled_at(naive, now=NOW) == SCHEDULE_NAIVE


def test_window_is_evaluated_in_absolute_time_not_wall_clock_text():
    """同一时刻用不同时区表达，结论必须一致。"""
    target = NOW + timedelta(hours=3)
    shanghai = target.astimezone(timezone(timedelta(hours=8)))
    assert validate_scheduled_at(shanghai, now=NOW) is None
    assert shanghai.utcoffset() != timedelta(0)  # 确实换了时区表达


# ── 自主声明：ai_content 与显式选择的关系 ─────────────────────


def test_ai_content_alone_maps_to_the_ai_declaration():
    """本次改动的核心：一个存了两个版本、从没生效过的布尔位，现在会变成
    页面上真实的一次点击。"""
    assert (
        resolve_self_declaration(ai_content=True, self_declaration=None)
        == SELF_DECLARATION_AI
    )


def test_explicit_declaration_overrides_ai_content():
    assert (
        resolve_self_declaration(
            ai_content=True, self_declaration=SELF_DECLARATION_REPOST
        )
        == SELF_DECLARATION_REPOST
    )


def test_explicit_no_declaration_overrides_ai_content_too():
    """标了 AI 又显式选「无需添加自主声明」—— 用户的显式选择胜出（UI 会提示
    冲突，但不替他改）。这条是产品决策，改它之前先读 resolve 的 docstring。"""
    assert (
        resolve_self_declaration(
            ai_content=True, self_declaration=SELF_DECLARATION_NONE
        )
        == SELF_DECLARATION_NONE
    )


def test_nothing_selected_leaves_the_control_untouched():
    assert resolve_self_declaration(ai_content=False, self_declaration=None) is None


def test_none_and_explicit_no_declaration_are_not_the_same_thing():
    """None = 不碰控件；'无需添加自主声明' = 用户显式点了那一项。"""
    assert SELF_DECLARATION_NONE is not None
    assert (
        resolve_self_declaration(
            ai_content=False, self_declaration=SELF_DECLARATION_NONE
        )
        == SELF_DECLARATION_NONE
    )


@pytest.mark.parametrize(
    "ai,decl,expected",
    [
        (True, None, False),  # 自动映射，不算冲突
        (True, SELF_DECLARATION_AI, False),
        (True, SELF_DECLARATION_REPOST, True),
        (True, SELF_DECLARATION_NONE, True),
        (False, SELF_DECLARATION_REPOST, False),
    ],
)
def test_conflict_detection(ai, decl, expected):
    assert self_declaration_conflicts(ai_content=ai, self_declaration=decl) is expected


def test_the_six_labels_are_exactly_the_platform_wording():
    """逐字对照抖音发布页（2026-08-06 实测）。这条不是重复定义 —— 它是
    "有人顺手把中文改成英文/改了标点"时唯一会响的警报。"""
    assert SELF_DECLARATIONS == (
        "内容由AI生成",
        "内容为个人观点或见解",
        "内容为转载信息",
        "内容含营销推广信息",
        "虚构演绎，仅供娱乐",
        "无需添加自主声明",
    )


# ── 合集 ────────────────────────────────────────────────────────


def test_collection_is_trimmed_and_blank_means_none():
    assert normalize_collection("  Summer Trip  ") == "Summer Trip"
    assert normalize_collection("   ") is None
    assert normalize_collection(None) is None


def test_absurdly_long_collection_name_is_rejected():
    with pytest.raises(ValueError):
        normalize_collection("x" * 101)


# ── 请求 schema：提交那一刻就拒（§7.7 第一道） ─────────────────


def _body(**over) -> dict:
    base = dict(
        content_type="video",
        resource_ids=["1"],
        title="Launch",
        account_ids=["900"],
    )
    base.update(over)
    return base


def _in_window() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=6)


def test_schema_accepts_a_valid_scheduled_batch():
    body = PublishTaskCreate(
        **_body(
            scheduled_at=_in_window(),
            self_declaration=SELF_DECLARATION_AI,
            collection_name="  Summer Trip ",
        )
    )
    assert body.self_declaration == SELF_DECLARATION_AI
    assert body.collection_name == "Summer Trip"


def test_schema_rejects_a_schedule_inside_the_two_hour_window():
    with pytest.raises(ValidationError) as exc:
        PublishTaskCreate(
            **_body(scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1))
        )
    assert "2 hours" in str(exc.value)


def test_schema_rejects_a_schedule_beyond_fourteen_days():
    with pytest.raises(ValidationError) as exc:
        PublishTaskCreate(
            **_body(scheduled_at=datetime.now(timezone.utc) + timedelta(days=15))
        )
    assert "14 days" in str(exc.value)


def test_schema_rejects_an_invented_self_declaration():
    """ "AI generated" 这种听着对的英文值必须被拒 —— 浏览器侧按原文匹配，
    一个翻译过的值只会导致选项没选中而作品照发。"""
    with pytest.raises(ValidationError):
        PublishTaskCreate(**_body(self_declaration="AI generated"))


@pytest.mark.parametrize("declaration", SELF_DECLARATIONS)
def test_schema_accepts_every_one_of_the_six(declaration):
    assert PublishTaskCreate(
        **_body(self_declaration=declaration)
    ).self_declaration == (declaration)


def test_schema_default_is_no_schedule_no_declaration_no_collection():
    """默认必须是"什么都不碰" —— 老调用方（没有这三个字段的前端）行为不变。"""
    body = PublishTaskCreate(**_body())
    assert body.scheduled_at is None
    assert body.self_declaration is None
    assert body.collection_name is None


# ── adapter fail-fast：起浏览器之前的第二道 ────────────────────


def _adapter() -> SessionAdapter:
    class _NoClient:
        pass

    return SessionAdapter("douyin", client=_NoClient())  # type: ignore[arg-type]


def _intent(**over) -> PublishIntent:
    base = dict(
        content_type="video",
        media=(PublishMedia(kind="video", url="https://s3/x.mp4", filename="x.mp4"),),
        title="Launch",
    )
    base.update(over)
    return PublishIntent(**base)  # type: ignore[arg-type]


def test_fail_fast_rejects_a_schedule_that_slipped_below_the_window():
    """请求时合法、真到执行时已经滑进 2 小时内 —— 这正是第二道存在的理由。"""
    problems = _adapter().validate_publish_intent(
        _intent(scheduled_at=NOW + timedelta(hours=2, minutes=5)), now=NOW
    )
    assert problems == [SCHEDULE_TOO_SOON]


def test_fail_fast_accepts_a_schedule_inside_the_window():
    assert (
        _adapter().validate_publish_intent(
            _intent(scheduled_at=NOW + timedelta(days=3)), now=NOW
        )
        == []
    )


def test_fail_fast_rejects_an_unknown_declaration_in_platform_options():
    problems = _adapter().validate_publish_intent(
        _intent(platform_options={"self_declaration": "内容由 AI 生成"}), now=NOW
    )
    assert any("unknown self declaration" in p for p in problems)


def test_fail_fast_accepts_a_verbatim_declaration_and_a_collection():
    assert (
        _adapter().validate_publish_intent(
            _intent(
                platform_options={
                    "self_declaration": SELF_DECLARATION_FICTION,
                    "collection": "Summer Trip",
                }
            ),
            now=NOW,
        )
        == []
    )


def test_fail_fast_rejects_an_empty_collection_name():
    problems = _adapter().validate_publish_intent(
        _intent(platform_options={"collection": "   "}), now=NOW
    )
    assert any("collection name is empty" in p for p in problems)


def test_unknown_platform_options_keys_still_pass_through_untouched():
    """platform_options 是逃生舱：只校验认识的键，否则每加一个平台专属字段
    都得先改 backend。"""
    intent = _intent(platform_options={"note_type": "whatever", "location": "Tokyo"})
    assert _adapter().validate_publish_intent(intent, now=NOW) == []
    assert intent.to_payload()["platform_options"]["note_type"] == "whatever"


def test_schedule_and_options_reach_the_wire_payload():
    payload = _intent(
        scheduled_at=NOW + timedelta(days=1),
        platform_options={"self_declaration": SELF_DECLARATION_AI},
    ).to_payload()
    assert payload["scheduled_at"] == (NOW + timedelta(days=1)).isoformat()
    assert payload["platform_options"]["self_declaration"] == SELF_DECLARATION_AI
