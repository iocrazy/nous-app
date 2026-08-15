"""配乐的结构化身份（``music_ref``，mig 429）从请求走到浏览器意图的那条链。

为什么这条链值得单独一个文件
============================
在它之前，配乐是一个字符串：用户打一个曲名，浏览器侧拿去平台搜索框里搜，搜到
同名那行就点。**实测（2026-08-15）说明曲名不是身份**——一次搜索里 5 条标题完全
相同、id 各异；而分类榜里的曲子按曲名去搜，返回的可能是一条标题一模一样、id
不同的歌。按名匹配在那条上判「精确命中」、点击成功、读回校验也过：每一道关卡
都亮绿灯，发出去的是另一首歌，而配乐发出去之后平台不让换。

所以这里钉住三件事，每一件都对应一种"看起来在工作、实际不在"的形态：

1. **两道门看到的是同一个请求** —— 提交门（``publish_gate``）与 workflow
   （``_build_publish_intent``）共用 ``music_platform_options``。抄成两份的
   后果不是报错，是提交时放行、执行时行为不同。
2. **``music_id`` 是字符串** —— 上游同时给 ``id``（JSON number，实测
   ``6953836671917951012``，超过 2^53）和 ``id_str``。误传前者要 422，不能
   静默降精度存下一个不存在的 id。
3. **残缺的 ref 要被拦住** —— 半个指纹不比曲名可信，而它在浏览器侧会走严格
   分支，判出来的"唯一命中"是假的。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.distribution_publish import PublishTaskCreate
from app.services.distribution.publish_gate import _platform_options
from app.services.distribution.publish_options import music_platform_options
from app.services.distribution.session_adapter import (
    SESSION_PLATFORM_PROFILES,
    SHAPE_MUSIC_REF_INVALID,
    _option_shape_problems,
)

pytestmark = pytest.mark.unit

#: 一条真实形状的引用。``music_id`` 是**字符串**（上游的 ``id_str``），这不是
#: 书写偏好：同一条结果里的 ``id`` 是 JSON number 且超过 2^53，写成整数的
#: fixture 会让测试证明一件真实载荷从不成立的事。
REF = {
    "music_id": "6953836671917951012",
    "music_name": "起风了",
    "music_author": "吴青峰",
    "duration": 325,
    "user_count": 30023,
    "cover_url": "https://p3.example.invalid/cover.jpeg",
}


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


# ── schema ────────────────────────────────────────────────────


def test_the_track_name_follows_the_reference_instead_of_the_client():
    """两个字段各写各的会漂：浏览器侧用 ``music_name`` 当搜索关键词、用
    ``music_ref`` 当对齐指纹。名字不是那首歌的名字，搜索结果里就根本不会有它
    ——表现为"选了歌却发布失败"，而根因是两份真相。"""
    body = _body(music_name="something the user left in the box", music_ref=REF)
    assert body.music_name == "起风了"


def test_a_numeric_id_is_refused_rather_than_silently_losing_precision():
    """**守卫**。上游一条结果同时有 ``id``（number）与 ``id_str``；误传前者，
    JS 侧的值早就不是那个数了。把它当字符串收下等于存一个不存在的曲目。

    把 ``music_id`` 改成 ``int | str`` 这条就红。
    """
    with pytest.raises(ValidationError):
        _body(music_ref={**REF, "music_id": 6953836671917951012})


def test_a_reference_without_an_id_never_becomes_a_task():
    with pytest.raises(ValidationError):
        _body(music_ref={**REF, "music_id": ""})


def test_no_reference_leaves_the_typed_name_path_exactly_as_it_was():
    body = _body(music_name="起风了")
    assert (body.music_name, body.music_ref) == ("起风了", None)


def test_no_music_at_all_stays_no_music():
    """``None`` = 不碰音乐控件 = 平台默认原声。这与"要配乐但没选上"是两件完全
    不同的事。"""
    body = _body()
    assert (body.music_name, body.music_ref) == (None, None)


# ── platform_options：提交门与 workflow 必须组装出同一份 ──────


def test_a_typed_name_produces_only_the_search_keyword():
    assert music_platform_options("起风了", None) == {"music": "起风了"}


def test_a_picked_card_produces_the_keyword_and_the_fingerprint():
    """``music`` 仍在，因为浏览器侧要拿它去平台搜索框里搜——它是关键词，不是
    判据。少了它连搜都搜不了。"""
    opts = music_platform_options(None, REF)
    assert opts["music"] == "起风了"
    assert opts["music_ref"]["music_id"] == REF["music_id"]
    assert opts["music_ref"]["music_author"] == "吴青峰"
    assert opts["music_ref"]["duration"] == 325


def test_the_upstreams_extra_fields_do_not_cross_the_service_boundary():
    """``platform_options`` 是原样透传到浏览器服务的逃生舱。往里塞一份完整的
    上游响应，等于把一个我们不控制形状的对象送过跨服务边界。"""
    opts = music_platform_options(None, {**REF, "play_url": "https://x/a.mp3"})
    assert "play_url" not in opts["music_ref"]
    assert "cover_url" not in opts["music_ref"]


def test_nothing_requested_touches_no_control():
    assert music_platform_options(None, None) == {}
    assert music_platform_options("   ", None) == {}


def test_the_submit_gate_and_the_workflow_assemble_the_same_options():
    """**守卫**。两处各抄一份的后果不是报错，是提交时放行、执行时做另一件事
    ——这条链上最难查的一类不一致。

    ``_build_publish_intent`` 读的是 DB 行（dict），提交门读的是请求体，所以
    这里比对的是"同一份数据经两条路径得到的结果"。
    """
    body = _body(music_ref=REF)
    from_gate = _platform_options(body)

    task_row = {"music_name": body.music_name, "music_ref": body.music_ref.model_dump()}
    from_workflow = music_platform_options(
        task_row["music_name"], task_row["music_ref"]
    )

    assert from_gate["music"] == from_workflow["music"]
    assert from_gate["music_ref"] == from_workflow["music_ref"]


# ── 形状校验：残缺的引用在提交那一刻就被拦 ────────────────────


def _reasons(problems):
    return [p.reason for p in problems]


def test_a_reference_missing_its_id_is_a_typed_refusal():
    profile = SESSION_PLATFORM_PROFILES["douyin"]
    problems = _option_shape_problems(
        profile, {"music": "起风了", "music_ref": {"music_name": "起风了"}}
    )
    assert SHAPE_MUSIC_REF_INVALID in _reasons(problems)


def test_a_reference_that_is_not_an_object_is_a_typed_refusal():
    profile = SESSION_PLATFORM_PROFILES["douyin"]
    problems = _option_shape_problems(
        profile, {"music": "起风了", "music_ref": "6953836671917951012"}
    )
    assert SHAPE_MUSIC_REF_INVALID in _reasons(problems)


def test_a_complete_reference_passes():
    profile = SESSION_PLATFORM_PROFILES["douyin"]
    assert (
        _option_shape_problems(profile, _platform_options(_body(music_ref=REF))) == []
    )


# ── 它真的进得了库、也读得回来 ────────────────────────────────


def test_the_column_exists_on_the_model_the_insert_is_built_from():
    """模型与迁移必须对得上。对不上时 ``create_task`` 不会静默丢字段，它会在
    真库上炸 —— 但那要等到部署之后。这一条把它提前到测试里。"""
    from app.models.distribution import PublishTasks

    assert "music_ref" in PublishTasks.__table__.columns


@pytest.mark.asyncio
async def test_the_reference_reaches_the_insert_statement():
    """**守卫**。往一个根本不会被写进 DB 的字段里存东西，看起来一直在工作、
    实际永远静默——本仓库刚栽过一次。所以这里不看返回值，看**真正编译出来的
    INSERT 到底带没带这一列**。

    删掉 ``create_task`` 里的 ``music_ref=...`` 这条就红。
    """
    from unittest.mock import patch

    from app.repositories.publish_tasks_repository import PublishTasksRepository

    captured: dict = {}

    class _Result:
        def mappings(self):
            class _M:
                def first(_self):
                    return {"id": 1, "title": "Summer Trip"}

            return _M()

    class _Session:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return _Result()

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *exc):
            return False

    with patch(
        "app.repositories.publish_tasks_repository.write_scope", lambda: _Scope()
    ):
        await PublishTasksRepository().create_task(
            user_id="u1",
            title="Summer Trip",
            music_name="起风了",
            music_ref=REF,
        )

    params = captured["stmt"].compile().params
    assert params["music_ref"] == REF
    assert params["music_name"] == "起风了"


def test_what_was_stored_is_read_back_to_the_client():
    """写进去却读不回来，就没人能证明它真的存下来了 —— 而这一条尤其要读得回
    来：它是"发出去的到底是哪一首"的唯一凭据。"""
    from app.api.distribution_router import _task_out

    out = _task_out(
        {
            "id": "1",
            "title": "Summer Trip",
            "created_at": "2026-08-15T00:00:00+00:00",
            "music_name": "起风了",
            "music_ref": REF,
        },
        [],
    )
    assert out.music_ref is not None
    assert out.music_ref.music_id == REF["music_id"]
    assert out.music_ref.user_count == 30023


def test_a_task_without_a_reference_reads_back_as_none_not_as_an_empty_object():
    """``None``（手打曲名 / 没要配乐）与"选了一首但字段是空的"必须一路可分辨。"""
    from app.api.distribution_router import _task_out

    out = _task_out(
        {
            "id": "1",
            "title": "Summer Trip",
            "created_at": "2026-08-15T00:00:00+00:00",
            "music_name": "起风了",
            "music_ref": None,
        },
        [],
    )
    assert out.music_ref is None
    assert out.music_name == "起风了"
