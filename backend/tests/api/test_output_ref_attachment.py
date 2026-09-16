"""三期 3a Task 4 / 3c §2.4：``output_ref`` 附件——引用一个产出版本。

⚠️ **归属在 3c 放宽了。** 3a 的规则是「这一版必须是本 issue 产的」；3c 起是
「这条链对调用方可见」，尺子是 ``deliverables/visibility.assert_chain_visible``
（血缘端点与回退端点用的同一把）。所以这里的桩打在**那把尺子**上，而不是
``lineage_for`` 上——桩在旧位置会让这个文件在一条生产已经不走的路上跑绿。

两条纪律在这个文件里同时受钉：

1. **静默丢附件是本仓明令禁止的**（「触发路径必须类型化失败回显」）。引用
   不可解析时必须是 400，而且 ``detail`` 必须是 **dict**——生产把每个
   ``HTTPException`` 包进 ``ErrorResponse`` 外壳，只有 dict 的 detail 会
   原样落到 ``details``，字符串会塌成 ``http_400`` + "400 Bad Request"，
   前端就再也读不到类型化的 code（CLAUDE.md 2026-09-09）。
2. **标题在发帖时从登记表抄一份**，存进消息的 attachments 里，线程渲染
   不需要第二次查询。
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.schemas.issue_message import IssueMessagePost

pytestmark = pytest.mark.unit

ME = "11111111-1111-1111-1111-111111111111"
AUTH = SimpleNamespace(user_id=UUID(ME))
ISSUE_ID = 5
OTHER_ISSUE_ID = 6


def _lineage_row(version: int, *, title: str | None, issue_id: int) -> dict:
    """``RunDeliverablesRepository.lineage_for`` 的一行（T3 的返回形状），也就是
    ``visible_chain`` 原样交出去的那个形状。"""
    return {
        "id": str(900 + version),
        "run_id": "700",
        "kind": "script_shot",
        "ref_id": "9",
        "version": version,
        "parent_version": version - 1 if version > 1 else None,
        "title": title,
        "issue_id": str(issue_id),
        "issue_key": f"MH-{issue_id}",
    }


def _att(**over) -> dict:
    base = {
        "kind": "output_ref",
        "ref_kind": "script_shot",
        "ref_id": "9",
        "version": 2,
    }
    base.update(over)
    return base


def _wire(monkeypatch, lineage: list[dict], *, chain_visible: bool = True):
    """路由 + 可见性尺子：让 post_issue_message 走到附件校验那一步。

    返回的第三个值是**那把尺子的间谍**：它取代了 3a 时代的 ``repo.lineage_for``
    间谍，因为解析器现在问的是 ``assert_chain_visible``。空链与
    ``chain_visible=False`` 都按真实实现那样抛 404 —— 「看不见」与「没登记」
    在那条路上本来就是同一个回答。
    """
    importlib.import_module("app.api.issue_messages_router")
    r = sys.modules["app.api.issue_messages_router"]

    issue_row = {
        "id": ISSUE_ID,
        "assignee_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "created_by_user_id": ME,
        "assignee_user_id": None,
        "paused_at": None,
        "execution_state": {},
    }
    monkeypatch.setattr(r, "_assert_issue_visible", AsyncMock(return_value=issue_row))
    monkeypatch.setattr(r, "get_or_create_issue_session", AsyncMock(return_value="55"))
    monkeypatch.setattr(r, "_divert_to_inbox_if_running", AsyncMock(return_value=None))
    monkeypatch.setattr(r, "_try_wake_waiting_workflow", AsyncMock(return_value=False))

    dispatch = AsyncMock(return_value=SimpleNamespace(mode="dispatched"))
    import app.services.issues.inbox_or_dispatch as _dispatch_mod

    monkeypatch.setattr(_dispatch_mod, "dispatch_issue_reply", dispatch)

    async def _chain(kind, ref_id, auth):
        if not chain_visible or not lineage:
            raise HTTPException(status_code=404, detail="not found")
        return lineage

    chain = AsyncMock(side_effect=_chain)
    import app.services.ai.chat.output_ref_resolver as _resolver_mod

    monkeypatch.setattr(_resolver_mod, "assert_chain_visible", chain)
    return r, dispatch, chain


def _wire_no_agent(monkeypatch):
    """同一个路由，但 issue 没有 assignee agent —— legacy 评论分支。

    只桩两处：可见性检查和 legacy 插入。**刻意不桩** session / dispatch 那几个，
    因为这条路径根本不该走到它们；万一将来走到了，缺桩会当场炸而不是静静地绿。
    """
    importlib.import_module("app.api.issue_messages_router")
    r = sys.modules["app.api.issue_messages_router"]

    issue_row = {
        "id": ISSUE_ID,
        "assignee_agent_id": None,
        "created_by_user_id": ME,
        "assignee_user_id": None,
        "paused_at": None,
        "execution_state": {},
    }
    monkeypatch.setattr(r, "_assert_issue_visible", AsyncMock(return_value=issue_row))
    legacy = AsyncMock(return_value=None)
    monkeypatch.setattr(r, "_insert_legacy_comment", legacy)
    return r, legacy


async def _post(r, attachments):
    return await r.post_issue_message(
        ISSUE_ID,
        IssueMessagePost(body="look at this", attachments=attachments),
        AUTH,
    )


# ── 不可解析 → 类型化 400 ────────────────────────────────────────────────


async def test_unresolvable_output_ref_is_a_typed_400(monkeypatch):
    """登记表里根本没有这个对象。"""
    r, dispatch, _ = _wire(monkeypatch, lineage=[])
    with pytest.raises(HTTPException) as exc:
        await _post(r, [_att(ref_id="999999", version=1)])
    assert exc.value.status_code == 400
    assert isinstance(
        exc.value.detail, dict
    ), "detail 必须是 dict，否则 details.code 丢失"
    assert exc.value.detail["code"] == "output_ref_unresolvable"
    dispatch.assert_not_awaited()


async def test_unknown_version_is_unresolvable(monkeypatch):
    """对象有 v1，用户引用的 v2 不存在。"""
    r, dispatch, _ = _wire(
        monkeypatch, lineage=[_lineage_row(1, title="S3 · Shot #1", issue_id=ISSUE_ID)]
    )
    with pytest.raises(HTTPException) as exc:
        await _post(r, [_att(version=2)])
    assert exc.value.detail["code"] == "output_ref_unresolvable"
    dispatch.assert_not_awaited()


async def test_citing_an_invisible_chain_is_unresolvable(monkeypatch):
    """3c §2.4 换掉的那条用例。旧契约是「别的 issue 的产出被拒」；新契约是
    「**看不见的链**被拒」——别的 issue 的产出只要看得见就可以引，而看不见的
    拿同一个 code：分出「存在但你不能引」等于确认那个对象存在。"""
    r, dispatch, _ = _wire(
        monkeypatch,
        lineage=[_lineage_row(2, title="S3 · Shot #1", issue_id=OTHER_ISSUE_ID)],
        chain_visible=False,
    )
    with pytest.raises(HTTPException) as exc:
        await _post(r, [_att()])
    assert exc.value.detail["code"] == "output_ref_unresolvable"
    dispatch.assert_not_awaited()


async def test_citing_another_visible_issues_output_now_resolves(monkeypatch):
    """放宽的正面那一半：同项目另一件议题的产出可以被引用，附件带上来源
    ``issue_key``，线程里的引用卡据它显示 chip。"""
    r, dispatch, _ = _wire(
        monkeypatch,
        lineage=[_lineage_row(2, title="S3 · Shot #1", issue_id=OTHER_ISSUE_ID)],
    )
    resp = await _post(r, [_att()])
    assert resp.agent_dispatched is True
    sent = dispatch.await_args.kwargs["attachments"]
    assert sent[0]["issue_key"] == f"MH-{OTHER_ISSUE_ID}"


async def test_unknown_ref_kind_is_unresolvable_without_touching_the_registry(
    monkeypatch,
):
    """四类之外的 kind 是接线错误，不该先花一次查询才发现。"""
    r, dispatch, chain = _wire(monkeypatch, lineage=[])
    with pytest.raises(HTTPException) as exc:
        await _post(r, [_att(ref_kind="script_chapterr")])
    assert exc.value.detail["code"] == "output_ref_unresolvable"
    chain.assert_not_awaited()


async def test_missing_version_is_unresolvable(monkeypatch):
    """引用的是**某一版**，没有版本号就不是引用。"""
    r, dispatch, _ = _wire(monkeypatch, lineage=[])
    with pytest.raises(HTTPException) as exc:
        await _post(
            r, [{"kind": "output_ref", "ref_kind": "script_shot", "ref_id": "9"}]
        )
    assert exc.value.detail["code"] == "output_ref_unresolvable"


# ── 可解析 → 盖上登记表的标题，继续原来的路 ──────────────────────────────


async def test_resolvable_output_ref_is_stamped_with_the_registry_title(monkeypatch):
    r, dispatch, _ = _wire(
        monkeypatch, lineage=[_lineage_row(2, title="S3 · Shot #1", issue_id=ISSUE_ID)]
    )
    resp = await _post(r, [_att(title="whatever the client typed")])
    assert resp.agent_dispatched is True
    sent = dispatch.await_args.kwargs["attachments"]
    assert sent == [
        {
            "kind": "output_ref",
            "ref_kind": "script_shot",
            "ref_id": "9",
            "version": 2,
            "title": "S3 · Shot #1",
            # 3c §2.4：来源议题也盖在附件上 —— 引用可以跨议题之后，chip 要说得出
            # 这一版是在哪件议题上产的。
            "issue_key": f"MH-{ISSUE_ID}",
        }
    ]


async def test_other_attachment_kinds_are_untouched(monkeypatch):
    """``output_ref`` 的校验不许改写别人的附件。"""
    r, dispatch, chain = _wire(monkeypatch, lineage=[])
    other = {"kind": "resource_ref", "resource_id": "42", "name": "spec.md"}
    resp = await _post(r, [other])
    assert resp.agent_dispatched is True
    chain.assert_not_awaited()
    sent = dispatch.await_args.kwargs["attachments"]
    assert sent[0]["kind"] == "resource_ref" and sent[0]["resource_id"] == "42"


async def test_one_object_cited_twice_costs_one_query(monkeypatch):
    r, dispatch, chain = _wire(
        monkeypatch,
        lineage=[
            _lineage_row(2, title="v2", issue_id=ISSUE_ID),
            _lineage_row(1, title="v1", issue_id=ISSUE_ID),
        ],
    )
    await _post(r, [_att(version=2), _att(version=1)])
    assert chain.await_count == 1
    sent = dispatch.await_args.kwargs["attachments"]
    assert [a["title"] for a in sent] == ["v2", "v1"]


async def test_over_cap_output_refs_are_a_typed_400(monkeypatch):
    """每条引用是一次查询。上限只活在前端选择器里，就是本仓记过的那类缺口。"""
    from app.services.ai.chat.output_ref_resolver import MAX_OUTPUT_REF_ATTACHMENTS

    r, dispatch, chain = _wire(monkeypatch, lineage=[])
    many = [_att(ref_id=str(i)) for i in range(MAX_OUTPUT_REF_ATTACHMENTS + 1)]
    with pytest.raises(HTTPException) as exc:
        await _post(r, many)
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "output_ref_limit_exceeded"
    chain.assert_not_awaited()


# ── 无 agent 的 issue：附件无人可读 → 类型化 409 ─────────────────────────


# `resource_ref` 的形状照抄前端真正发出去的那份（`IssueReplyBox.tsx::collectRefs`），
# 不是按后端读起来顺眼的样子写的——「边界 mock 必须用真实 JSON 形状」。
_RESOURCE_REF = {
    "kind": "resource_ref",
    "resource_id": "42",
    "name": "spec.md",
    "mime": "text/markdown",
    "scope": {"type": "personal", "id": ""},
}


@pytest.mark.parametrize(
    "attachment",
    [
        pytest.param(_att(), id="output_ref"),
        pytest.param(_RESOURCE_REF, id="resource_ref"),
        pytest.param({"kind": "image", "url": "https://x/y.png"}, id="image"),
    ],
)
async def test_an_attachment_on_an_agentless_issue_is_a_typed_409(
    monkeypatch, attachment
):
    """C17：legacy 评论路径在 3a 之后成了唯一静默吃附件的分支——而它吃的不止
    引用：`_insert_legacy_comment` 只落 body + meta={}，**每一种** kind 都消失。
    附件只有 agent 读得懂，没有 agent 就该当面拒绝，而不是发帖成功、附件消失。"""
    r, legacy = _wire_no_agent(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await _post(r, [attachment])
    assert exc.value.status_code == 409
    assert isinstance(
        exc.value.detail, dict
    ), "detail 必须是 dict，否则 details.code 丢失"
    assert exc.value.detail["code"] == "citations_need_agent"
    legacy.assert_not_awaited()  # 一行都不许落库


async def test_an_ordinary_comment_on_an_agentless_issue_still_posts(monkeypatch):
    """守卫只认附件：没有附件的普通评论照旧走 legacy 插入，一个字都没变。"""
    r, legacy = _wire_no_agent(monkeypatch)
    await r.post_issue_message(ISSUE_ID, IssueMessagePost(body="hi"), AUTH)
    legacy.assert_awaited_once()
    await r.post_issue_message(
        ISSUE_ID, IssueMessagePost(body="still here", attachments=[]), AUTH
    )
    assert legacy.await_count == 2  # 空列表不是「有东西要交给 agent」


async def test_empty_registry_title_is_absence_not_an_empty_string(monkeypatch):
    """B1：登记表里的 ``""`` 与 ``None`` 是同一件事——「没有标题」。

    留着空串会一路走到 ``<referenced_outputs>`` 渲染成 ``title=""``，对模型
    而言那读作「它的标题就是空字符串」，与 docstring 承诺的「省略」相反。
    """
    r, dispatch, _ = _wire(
        monkeypatch, lineage=[_lineage_row(2, title="   ", issue_id=ISSUE_ID)]
    )
    resp = await _post(r, [_att(title="whatever the client typed")])
    assert resp.agent_dispatched is True
    sent = dispatch.await_args.kwargs["attachments"]
    assert sent == [
        {
            "kind": "output_ref",
            "ref_kind": "script_shot",
            "ref_id": "9",
            "version": 2,
            "issue_key": f"MH-{ISSUE_ID}",
        }
    ]


async def test_a_padded_registry_title_is_stamped_trimmed(monkeypatch):
    """评审 N2 的另一半：归一发生在构造 ``ChatOutputRef`` 的地方。

    上面那条（框渲染）证明模型看到的是 ``title="S3 · Shot #1"``；这一条证明
    存进消息 ``attachments`` 的也是同一个值——两个读者读同一次归一，不是各
    自 strip 一遍。
    """
    r, dispatch, _ = _wire(
        monkeypatch,
        lineage=[_lineage_row(2, title="  S3 · Shot #1  ", issue_id=ISSUE_ID)],
    )
    resp = await _post(r, [_att()])
    assert resp.agent_dispatched is True
    assert dispatch.await_args.kwargs["attachments"][0]["title"] == "S3 · Shot #1"
