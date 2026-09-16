"""`RunRecorder._finish` **不是** agent_runs 唯一的终态写方（3c Task 13 评审）。

清扫器也会终结一条 run，而被它终结的 run 永远不会再经过 `_finish`：

- `liveness_scanner._mark_dead` —— CAS running → failed，判「进程真的死了」；
- `AgentRunsRepository.mark_heartbeat_lost_ids` —— 批量 running → heartbeat_lost。

少了投影，这些 run 在检索里停在它们 `running` 时的样子，或者干脆不存在；而
**缺席是静默的** —— 没有任何断言、日志或指标会说出来。这几条就是那个哨兵。

两个方向都要钉：终态写入之后投影跟上；投影炸了终态写入照样成立（best-effort
绝不连坐，同 `register_deliverable_best_effort`）。
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from app.services.search import projection as mod

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"
RUN_ID = 913000000000001


class _RepoSpy:
    def __init__(self, boom: bool = False) -> None:
        self.docs, self._boom = [], boom

    async def upsert(self, doc):
        if self._boom:
            raise RuntimeError("relation search_docs does not exist")
        self.docs.append(doc)


class _Result:
    """够 `_mark_dead` 读 rowcount、够心跳清扫读 RETURNING 的最小结果对象。"""

    def __init__(self, *, rowcount: int = 1, rows: tuple = ()) -> None:
        self.rowcount = rowcount
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Session:
    def __init__(self, result: _Result) -> None:
        self.statements, self._result = [], result

    async def execute(self, statement):
        self.statements.append(statement)
        return self._result


def _scope_for(session: _Session):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


@pytest.fixture
def spy(monkeypatch):
    s = _RepoSpy()
    monkeypatch.setattr(mod, "get_search_docs_repository", lambda: s)
    return s


@pytest.fixture
def run_row(monkeypatch):
    """终态 UPDATE 提交之后读回来的那一行。投影**必须**读库而不是用内存里的
    值——写方各有各的字段，只有库说得出这次终态到底落成什么样。"""
    asked: list = []

    async def _row(run_id):
        asked.append(run_id)
        return {
            "id": RUN_ID,
            "issue_id": None,
            "team_id": 7,
            "project_id": None,
            "agent_id": None,
            "user_id": ME,
            "model": "doubao",
            "status": "failed",
            "error_code": "no_heartbeat",
            "input_summary": "render the rain pass",
            "output_summary": None,
        }

    monkeypatch.setattr(mod, "_run_row", _row)
    return asked


async def test_a_run_the_scanner_kills_reaches_search_docs(spy, run_row, monkeypatch):
    from app.workflows import liveness_scanner

    session = _Session(_Result(rowcount=1))
    monkeypatch.setattr("app.db.session.write_scope", _scope_for(session))

    await liveness_scanner._mark_dead(RUN_ID, "stuck", reason="no_heartbeat")

    assert session.statements, "the CAS update must still run"
    assert run_row == [RUN_ID]
    assert len(spy.docs) == 1
    doc = spy.docs[-1]
    # run 行的身份键是 str(agent_runs.id) —— 与 _finish 投影的那一条同一个键，
    # 所以清扫器终结的是**同一条**投影，不是第二条。
    assert (doc.entity_kind, doc.entity_id) == ("run", str(RUN_ID))
    assert (doc.status, doc.error_code) == ("failed", "no_heartbeat")


async def test_a_cas_that_matched_nothing_projects_nothing(spy, run_row, monkeypatch):
    """CAS 没命中 = 别人已经把这条 run 收工了。再投一次会用清扫器视角覆盖掉
    那个真正的终态。"""
    from app.workflows import liveness_scanner

    monkeypatch.setattr(
        "app.db.session.write_scope", _scope_for(_Session(_Result(rowcount=0)))
    )
    await liveness_scanner._mark_dead(RUN_ID, "stuck", reason="no_heartbeat")
    assert spy.docs == [] and run_row == []


async def test_a_failing_projection_does_not_undo_the_kill(run_row, monkeypatch):
    from app.workflows import liveness_scanner

    monkeypatch.setattr(mod, "get_search_docs_repository", lambda: _RepoSpy(boom=True))
    session = _Session(_Result(rowcount=1))
    monkeypatch.setattr("app.db.session.write_scope", _scope_for(session))

    await liveness_scanner._mark_dead(RUN_ID, "stuck", reason="no_heartbeat")

    # 没有异常穿出来，而那次 CAS 已经发生过了。
    assert session.statements


async def test_every_heartbeat_lost_run_reaches_search_docs(spy, run_row, monkeypatch):
    from app.repositories import agent_runs_repository as repo_mod

    session = _Session(_Result(rows=((RUN_ID,),)))
    monkeypatch.setattr(repo_mod, "write_scope", _scope_for(session))

    ids = await repo_mod.AgentRunsRepository().mark_heartbeat_lost_ids(
        stale_before=datetime.now(timezone.utc)
    )

    assert ids == [RUN_ID] and run_row == [RUN_ID]
    assert [d.entity_id for d in spy.docs] == [str(RUN_ID)]
    assert spy.docs[-1].status == "failed"


async def test_a_failing_projection_still_reports_the_swept_ids(run_row, monkeypatch):
    """扫出来的 id 是调用方用来补 ``turn_end`` 事件的（mig 453 spine）。投影
    失败把它吞成空列表，就等于让那些 run 的 transcript 永远缺终止事件。"""
    from app.repositories import agent_runs_repository as repo_mod

    monkeypatch.setattr(mod, "get_search_docs_repository", lambda: _RepoSpy(boom=True))
    monkeypatch.setattr(
        repo_mod, "write_scope", _scope_for(_Session(_Result(rows=((RUN_ID,),))))
    )

    ids = await repo_mod.AgentRunsRepository().mark_heartbeat_lost_ids(
        stale_before=datetime.now(timezone.utc)
    )
    assert ids == [RUN_ID]
