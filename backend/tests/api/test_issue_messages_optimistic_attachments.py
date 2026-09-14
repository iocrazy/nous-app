"""三期 3a 小票 A7：``POST /issues/{id}/messages`` 的乐观 comment 带 attachments。

POST 在五个返回点上合成 ``comment``（``_optimistic_comment``）。在此之前它只
带 ``body``，``attachments`` 永远缺席——于是同一条消息，POST 响应里没有 chip、
GET 读回路里有 chip，两个口径不一样。

这个文件钉住的是**同一形状**：响应里的 ``comment.attachments`` 必须先过
``ConversationsAiStore.display_attachments``（store 的白名单，字节不进消息体），
再过 ``issue_message_mapper`` 的逐条投影，也就是 ``GET /messages`` 交出来的那个
``IssueMessageAttachment``——``ref_id`` 是 string、``title`` 是发帖时抄下的快照、
没附件时是 ``None`` 而不是 ``[]``（「absent 是诚实的答案」）。

wire 形状照抄真实响应体（CLAUDE.md「边界 mock 必须用真实 JSON 形状」）：
``ref_id`` 是 string 化的 snowflake，不是理想化的 ``"9"``。
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.issue_message import IssueMessagePost

pytestmark = pytest.mark.unit

ME = "11111111-1111-1111-1111-111111111111"
AGENT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
AUTH = SimpleNamespace(user_id=UUID(ME))
ISSUE_ID = 5
#: 真实 wire 形状：登记表主键是 Snowflake BIGINT，前端拿到的是 string。
REF_ID = "337650953731886"
TITLE = "S3 · Shot #1"

OUTPUT_REF = {
    "kind": "output_ref",
    "ref_kind": "script_shot",
    "ref_id": REF_ID,
    "version": 1,
}

#: 一张图的真实上行形状——``data_url`` 字节、``scope`` 提示都在里面，两者都
#: 不该出现在响应的 comment 上。
FILE_ATTACHMENT = {
    "kind": "image",
    "mime": "image/png",
    "name": "shot.png",
    "alt_text": "the shot",
    "data_url": "data:image/png;base64,AAAA",
    "url": "https://example.test/shot.png",
    "scope": {"team_id": "1"},
}


def _lineage_row(version: int = 1, *, title: str | None = TITLE) -> dict:
    """``RunDeliverablesRepository.lineage_for`` 的一行。"""
    return {
        "id": "901",
        "run_id": "700",
        "kind": "script_shot",
        "ref_id": REF_ID,
        "version": version,
        "parent_version": None,
        "title": title,
        "issue_id": str(ISSUE_ID),
    }


def _wire(monkeypatch, *, lineage: list[dict] | None = None, divert_id=None):
    """路由 + 登记表 + store：让 post_issue_message 跑完一条完整路径。"""
    importlib.import_module("app.api.issue_messages_router")
    r = sys.modules["app.api.issue_messages_router"]

    issue_row = {
        "id": ISSUE_ID,
        "assignee_agent_id": AGENT,
        "created_by_user_id": ME,
        "assignee_user_id": None,
        "paused_at": None,
        "execution_state": {},
    }
    monkeypatch.setattr(r, "_assert_issue_visible", AsyncMock(return_value=issue_row))
    monkeypatch.setattr(r, "get_or_create_issue_session", AsyncMock(return_value="55"))
    monkeypatch.setattr(
        r, "_divert_to_inbox_if_running", AsyncMock(return_value=divert_id)
    )
    monkeypatch.setattr(r, "_try_wake_waiting_workflow", AsyncMock(return_value=False))

    dispatch = AsyncMock(return_value=SimpleNamespace(mode="dispatched"))
    import app.services.issues.inbox_or_dispatch as _dispatch_mod

    monkeypatch.setattr(_dispatch_mod, "dispatch_issue_reply", dispatch)

    repo = SimpleNamespace(lineage_for=AsyncMock(return_value=lineage or []))
    import app.services.ai.chat.output_ref_resolver as _resolver_mod

    monkeypatch.setattr(_resolver_mod, "get_run_deliverables_repository", lambda: repo)

    # Note 路径的 store。``display_attachments`` 委托给真货——被测的正是「响应
    # 有没有过这个 reducer」，桩掉它就等于把结论先写进 mock 里。
    append = AsyncMock(return_value={})
    real_reduce = r.ConversationsAiStore.display_attachments

    class _Store:
        display_attachments = staticmethod(real_reduce)

        async def append_user_message(self, **kwargs):
            return await append(**kwargs)

    monkeypatch.setattr(r, "ConversationsAiStore", _Store)

    return SimpleNamespace(r=r, dispatch=dispatch, append=append, repo=repo)


async def _post(w, attachments, *, suppress=None, body="look at this"):
    return await w.r.post_issue_message(
        ISSUE_ID,
        IssueMessagePost(
            body=body, attachments=attachments, suppress_agent_ids=suppress
        ),
        AUTH,
    )


# ── Wake 路径（dispatch） ────────────────────────────────────────────────


async def test_wake_path_comment_carries_the_stamped_citation(monkeypatch):
    """引用解析时盖上的 title 必须随响应回来，``ref_id`` 保持 string。"""
    w = _wire(monkeypatch, lineage=[_lineage_row()])
    resp = await _post(w, [OUTPUT_REF])

    assert resp.agent_dispatched is True
    atts = resp.comment.attachments
    assert atts is not None and len(atts) == 1
    one = atts[0]
    assert one.kind == "output_ref"
    assert one.ref_kind == "script_shot"
    assert one.ref_id == REF_ID and isinstance(one.ref_id, str)
    assert one.version == 1
    assert one.title == TITLE


async def test_a_file_attachment_never_hands_back_bytes(monkeypatch):
    """``data_url`` / ``url`` / ``scope`` 不进 store，也不该进响应。"""
    w = _wire(monkeypatch)
    resp = await _post(w, [FILE_ATTACHMENT])

    atts = resp.comment.attachments
    assert atts is not None and len(atts) == 1
    dumped = atts[0].model_dump()
    for forbidden in ("data_url", "url", "scope"):
        assert forbidden not in dumped, f"{forbidden} 不该出现在响应的 comment 上"
    assert dumped["kind"] == "image"
    assert dumped["mime"] == "image/png"
    assert dumped["name"] == "shot.png"
    assert dumped["alt_text"] == "the shot"


async def test_no_attachments_reads_back_as_null(monkeypatch):
    """与老行读回路一致：没有就是 ``None``，不是 ``[]``。"""
    w = _wire(monkeypatch)
    assert (await _post(w, None)).comment.attachments is None
    assert (await _post(w, [])).comment.attachments is None


# ── Note 路径（suppressed） ─────────────────────────────────────────────


async def test_note_path_comment_matches_what_was_stored(monkeypatch):
    """响应的 comment 与 GET 读回同一条消息时的形状必须逐字段相等。"""
    from app.services.issues.issue_message_mapper import (
        map_ai_message_to_issue_message,
    )

    w = _wire(monkeypatch, lineage=[_lineage_row()])
    resp = await _post(w, [OUTPUT_REF, FILE_ATTACHMENT], suppress=[AGENT])

    assert resp.agent_dispatched is False
    stored = w.append.await_args.kwargs["attachments"]
    assert stored, "note 路径本来就该存下附件"

    # 同一批存下来的附件，走 GET 的读回路投影一次。
    read_back = map_ai_message_to_issue_message(
        {
            "id": 9,
            "role": "user",
            "content": "look at this",
            "attachments": stored,
            "created_at": "2026-09-13T10:00:00+00:00",
        },
        issue_id=ISSUE_ID,
        session_user_id=None,
    ).attachments

    assert resp.comment.attachments == read_back


async def test_slash_note_body_also_carries_the_attachments(monkeypatch):
    w = _wire(monkeypatch, lineage=[_lineage_row()])
    resp = await _post(w, [OUTPUT_REF], body="/note keep this one")

    assert resp.agent_dispatched is False
    assert [a.title for a in (resp.comment.attachments or [])] == [TITLE]


# ── Inbox 转投 / 等待中的 workflow / 定型答案 ───────────────────────────


async def test_inbox_diverted_comment_carries_the_attachments(monkeypatch):
    w = _wire(monkeypatch, lineage=[_lineage_row()], divert_id="77")
    resp = await _post(w, [OUTPUT_REF])

    assert resp.diverted_to_inbox is True
    assert [a.ref_id for a in (resp.comment.attachments or [])] == [REF_ID]


async def test_waiting_workflow_comment_carries_the_attachments(monkeypatch):
    w = _wire(monkeypatch, lineage=[_lineage_row()])
    monkeypatch.setattr(w.r, "_try_wake_waiting_workflow", AsyncMock(return_value=True))
    resp = await _post(w, [OUTPUT_REF])

    assert resp.agent_dispatched is True
    w.dispatch.assert_not_awaited()
    assert [a.ref_id for a in (resp.comment.attachments or [])] == [REF_ID]


async def test_typed_answer_that_ends_the_issue_carries_the_attachments(monkeypatch):
    """答案终结了 issue（例如预算 Cancel）：不唤醒任何人，comment 照样带附件。"""
    w = _wire(monkeypatch, lineage=[_lineage_row()])
    ended = w.r._PendingAnswer(
        question_id="q1",
        value="Cancel",
        kind="budget",
        run_id="700",
        workflow_id="wf-1",
        wake=False,
    )
    monkeypatch.setattr(w.r, "_validate_typed_answer", AsyncMock(return_value=ended))
    monkeypatch.setattr(w.r, "_commit_typed_answer", AsyncMock(return_value=None))

    resp = await w.r.post_issue_message(
        ISSUE_ID,
        IssueMessagePost(body="Cancel", answer_to="q1", attachments=[OUTPUT_REF]),
        AUTH,
    )

    assert resp.agent_dispatched is False
    w.dispatch.assert_not_awaited()
    assert [a.title for a in (resp.comment.attachments or [])] == [TITLE]
