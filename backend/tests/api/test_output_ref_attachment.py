"""三期 3a Task 4：``output_ref`` 附件——引用本 issue 的一个产出版本。

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
    """``RunDeliverablesRepository.lineage_for`` 的一行（T3 的返回形状）。"""
    return {
        "id": str(900 + version),
        "run_id": "700",
        "kind": "script_shot",
        "ref_id": "9",
        "version": version,
        "parent_version": version - 1 if version > 1 else None,
        "title": title,
        "issue_id": str(issue_id),
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


def _wire(monkeypatch, lineage: list[dict]):
    """路由 + 登记表：让 post_issue_message 走到附件校验那一步。"""
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

    repo = SimpleNamespace(lineage_for=AsyncMock(return_value=lineage))
    import app.services.ai.chat.output_ref_resolver as _resolver_mod

    monkeypatch.setattr(_resolver_mod, "get_run_deliverables_repository", lambda: repo)
    return r, dispatch, repo


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


async def test_citing_another_issues_output_is_unresolvable(monkeypatch):
    """行存在，但它的 run 属于别的 issue——同一个 code，消息说得出原因。"""
    r, dispatch, _ = _wire(
        monkeypatch,
        lineage=[_lineage_row(2, title="S3 · Shot #1", issue_id=OTHER_ISSUE_ID)],
    )
    with pytest.raises(HTTPException) as exc:
        await _post(r, [_att()])
    assert exc.value.detail["code"] == "output_ref_unresolvable"
    dispatch.assert_not_awaited()


async def test_unknown_ref_kind_is_unresolvable_without_touching_the_registry(
    monkeypatch,
):
    """四类之外的 kind 是接线错误，不该先花一次查询才发现。"""
    r, dispatch, repo = _wire(monkeypatch, lineage=[])
    with pytest.raises(HTTPException) as exc:
        await _post(r, [_att(ref_kind="script_chapterr")])
    assert exc.value.detail["code"] == "output_ref_unresolvable"
    repo.lineage_for.assert_not_awaited()


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
        }
    ]


async def test_other_attachment_kinds_are_untouched(monkeypatch):
    """``output_ref`` 的校验不许改写别人的附件。"""
    r, dispatch, repo = _wire(monkeypatch, lineage=[])
    other = {"kind": "resource_ref", "resource_id": "42", "name": "spec.md"}
    resp = await _post(r, [other])
    assert resp.agent_dispatched is True
    repo.lineage_for.assert_not_awaited()
    sent = dispatch.await_args.kwargs["attachments"]
    assert sent[0]["kind"] == "resource_ref" and sent[0]["resource_id"] == "42"


async def test_one_object_cited_twice_costs_one_query(monkeypatch):
    r, dispatch, repo = _wire(
        monkeypatch,
        lineage=[
            _lineage_row(2, title="v2", issue_id=ISSUE_ID),
            _lineage_row(1, title="v1", issue_id=ISSUE_ID),
        ],
    )
    await _post(r, [_att(version=2), _att(version=1)])
    assert repo.lineage_for.await_count == 1
    sent = dispatch.await_args.kwargs["attachments"]
    assert [a["title"] for a in sent] == ["v2", "v1"]


async def test_over_cap_output_refs_are_a_typed_400(monkeypatch):
    """每条引用是一次查询。上限只活在前端选择器里，就是本仓记过的那类缺口。"""
    from app.services.ai.chat.output_ref_resolver import MAX_OUTPUT_REF_ATTACHMENTS

    r, dispatch, repo = _wire(monkeypatch, lineage=[])
    many = [_att(ref_id=str(i)) for i in range(MAX_OUTPUT_REF_ATTACHMENTS + 1)]
    with pytest.raises(HTTPException) as exc:
        await _post(r, many)
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "output_ref_limit_exceeded"
    repo.lineage_for.assert_not_awaited()
