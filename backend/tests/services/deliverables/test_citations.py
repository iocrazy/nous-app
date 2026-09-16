"""引用镜像（3c §2.2）。「这一版产出被谁引用过」今天无法回答——引用只活在
``messages.body`` 的 jsonb 里，那一列零索引（侦察 C4）。对照组是画布侧的
``canvas_asset_refs``：反查靠物化镜像表解决。"""

import pytest
from loguru import logger as _loguru

from app.services.deliverables import citations as mod

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"
REF = {"kind": "output_ref", "ref_kind": "script_shot", "ref_id": "9", "version": 4}


@pytest.fixture
def caplog(caplog):
    """本模块经 loguru 记日志，它不往 stdlib 的捕获里传播（同
    ``test_ai_intent_fields`` / ``test_codex_daemon_adapter``）。"""
    handler_id = _loguru.add(caplog.handler, format="{message}", level="WARNING")
    yield caplog
    _loguru.remove(handler_id)


class _RepoSpy:
    def __init__(self, boom=False):
        self.batches, self._boom = [], boom

    async def insert_many(self, rows, *, session=None):
        if self._boom:
            raise RuntimeError("relation output_citations does not exist")
        self.batches.append(rows)


@pytest.fixture
def spy(monkeypatch):
    s = _RepoSpy()
    monkeypatch.setattr(mod, "get_output_citations_repository", lambda: s)
    return s


async def _record(attachments):
    await mod.record_output_citations(
        None,
        message_row={"id": "5001", "conversation_id": "700"},
        attachments=attachments,
        user_id=ME,
        issue_id=96,
        conversation_id=700,
    )


async def test_only_output_refs_are_mirrored(spy):
    await _record(
        [
            {"kind": "resource_ref", "resource_id": "1"},
            dict(REF),
            {"kind": "asset_ref", "asset_id": "2"},
        ]
    )
    assert spy.batches[-1] == [
        {
            "kind": "script_shot",
            "ref_id": "9",
            "version": 4,
            "issue_id": 96,
            "conversation_id": 700,
            "message_id": 5001,
            "cited_by_user_id": ME,
        }
    ]


async def test_no_citations_touches_the_table_at_all(spy):
    await _record([{"kind": "resource_ref"}])
    await _record(None)
    assert spy.batches == []


async def test_the_same_version_cited_twice_in_one_message_is_one_row(spy):
    await _record([dict(REF), dict(REF)])
    # UNIQUE (message_id, kind, ref_id, version) 会拦，但让它走到数据库再靠
    # 约束兜底，等于每次都赌 INSERT 的失败模式。在这里去重。
    assert len(spy.batches[-1]) == 1


async def test_a_malformed_citation_is_skipped_and_logged(spy, caplog):
    await _record([{"kind": "output_ref", "ref_kind": "script_shot", "ref_id": "9"}])
    # 没有 version 的引用只能来自「发帖口没校验就落库」的接线漂移。静默跳过
    # 会让那种漂移永远不被发现。
    assert spy.batches == []
    assert any("citation" in r.message for r in caplog.records)


async def test_a_failing_mirror_never_unposts_the_message(monkeypatch, caplog):
    monkeypatch.setattr(
        mod, "get_output_citations_repository", lambda: _RepoSpy(boom=True)
    )
    await _record([dict(REF)])
    assert any("output_citations" in r.message for r in caplog.records)


# ── 写点接线（3c §2.2） ───────────────────────────────────────────────────


async def _append(monkeypatch, attachments, *, issue=None):
    """跑真的 ``ConversationsAiStore.append_user_message``，只桩它的两个外部面。

    **写点唯一这件事只能在这里被证明。** 断言 ``record_output_citations`` 被调
    到，等于断言「议题评论 / 唤醒注入 / 聊天面板三条入口共用的那一个落库方法
    会物化反查」——桩掉 append_user_message 自己就等于把结论先写进 mock 里。
    """
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    seen = {"calls": [], "sessions": []}

    async def _send_message(**kw):
        return {"id": 5001, "conversation_id": 700, "seq": 1, "created_at": None}

    async def _get_by_session(session_id):
        seen["sessions"].append(session_id)
        return issue

    async def _record(session, **kw):
        seen["calls"].append(kw)

    import app.repositories.conversation_repository as _conv
    import app.repositories.issue_repository as _issues

    monkeypatch.setattr(
        _conv,
        "get_conversation_repository",
        lambda: type("R", (), {"send_message": staticmethod(_send_message)})(),
    )
    monkeypatch.setattr(_issues.issue_repository, "get_by_session", _get_by_session)
    monkeypatch.setattr(mod, "record_output_citations", _record)

    await ConversationsAiStore().append_user_message(
        session_id=700, user_id=ME, content="look at this", attachments=attachments
    )
    return seen


async def test_the_single_write_point_mirrors_a_citation(monkeypatch):
    seen = await _append(monkeypatch, [dict(REF)], issue={"id": 96})
    assert seen["sessions"] == [700]
    assert seen["calls"][0]["issue_id"] == 96
    assert seen["calls"][0]["conversation_id"] == 700
    assert seen["calls"][0]["message_row"]["id"] == 5001


async def test_an_ordinary_message_costs_no_extra_round_trip(monkeypatch):
    """议题只在真有引用时才查。普通消息（绝大多数）不该为反查多付一次查询。"""
    seen = await _append(monkeypatch, [{"kind": "image", "mime": "image/png"}])
    assert seen["sessions"] == [] and seen["calls"] == []
    seen = await _append(monkeypatch, None)
    assert seen["sessions"] == [] and seen["calls"] == []


async def test_a_failing_issue_lookup_never_unposts_the_message(monkeypatch, caplog):
    """议题反查在写点的 try **内**（修复轮 1）。

    消息此刻已经落库并提交了（``send_message`` 自开自提交）。这次读失败穿出
    ``append_user_message``，注释路径会把它翻成 500「failed to save note」，
    把一条**已经保存的**注释报成没保存。镜像失败的正确表现是「消息发出去了，
    反查没建上」。
    """
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    async def _send_message(**kw):
        return {"id": 5001, "conversation_id": 700, "seq": 1, "created_at": None}

    async def _boom(session_id):
        raise RuntimeError("connection reset by peer")

    recorded = []

    import app.repositories.conversation_repository as _conv
    import app.repositories.issue_repository as _issues

    monkeypatch.setattr(
        _conv,
        "get_conversation_repository",
        lambda: type("R", (), {"send_message": staticmethod(_send_message)})(),
    )
    monkeypatch.setattr(_issues.issue_repository, "get_by_session", _boom)
    monkeypatch.setattr(
        mod, "record_output_citations", lambda *a, **k: recorded.append(k)
    )

    out = await ConversationsAiStore().append_user_message(
        session_id=700, user_id=ME, content="look at this", attachments=[dict(REF)]
    )
    assert out["id"] == 5001 and out["role"] == "user"
    assert recorded == []  # 反查没建上——但消息保住了
    assert any("issue lookup FAILED" in r.message for r in caplog.records)
