"""两个 best_effort 写方（3c §2.1）。纪律同 register_deliverable_best_effort：
产物已经存在，投影失败不该把它判成失败。**不是静默吞错**——失败记 ERROR。"""

import pytest
from loguru import logger

from app.services.deliverables.registry import DeliverableRow
from app.services.search import projection as mod

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"


class _RepoSpy:
    def __init__(self, boom=False):
        self.docs, self._boom = [], boom

    async def upsert(self, doc):
        if self._boom:
            raise RuntimeError("relation search_docs does not exist")
        self.docs.append(doc)


@pytest.fixture
def spy(monkeypatch):
    s = _RepoSpy()
    monkeypatch.setattr(mod, "get_search_docs_repository", lambda: s)
    return s


async def test_a_run_projects_with_its_issue_key_and_title(spy, monkeypatch):
    async def _ident(issue_id):
        assert issue_id == 96
        return ("MH-96", "Alpha rain on glass")

    monkeypatch.setattr(mod, "_issue_identity", _ident)
    await mod.project_run_best_effort(
        {
            "id": 913,
            "issue_id": 96,
            "team_id": 7,
            "project_id": 3,
            "agent_id": "a-1",
            "user_id": ME,
            "model": "doubao",
            "status": "completed",
            "error_code": None,
            "input_summary": "ignored",
            "output_summary": "6403 images",
        }
    )
    doc = spy.docs[-1]
    assert (doc.entity_kind, doc.entity_id) == ("run", "913")
    assert doc.title == "MH-96 · Alpha rain on glass" and doc.body == "6403 images"
    assert (doc.team_id, doc.issue_id, doc.run_id, doc.owner_user_id) == (
        7,
        96,
        913,
        ME,
    )


async def test_a_run_without_an_issue_falls_back_to_a_clipped_input_summary(spy):
    await mod.project_run_best_effort(
        {
            "id": 914,
            "issue_id": None,
            "user_id": ME,
            "status": "failed",
            "error_code": "budget_exhausted",
            "input_summary": "x" * 200,
        }
    )
    doc = spy.docs[-1]
    assert doc.title == "x" * 80  # 首 80 字，不是整段
    # 无议题的 run 靠 owner 做可见性——SQL 层唯一能证明归属的列。
    assert doc.owner_user_id == ME and doc.error_code == "budget_exhausted"


async def test_a_run_with_neither_issue_nor_summary_still_gets_a_title(spy):
    await mod.project_run_best_effort({"id": 915, "issue_id": None, "user_id": ME})
    # title NOT NULL —— 编不出名字也必须写一条，否则这次运行在检索里不存在。
    assert spy.docs[-1].title == "run"


async def test_an_output_carries_the_search_text_as_its_body(spy, monkeypatch):
    async def _coords(run_id):
        assert run_id == "913"
        return {
            "team_id": 7,
            "project_id": 3,
            "issue_id": 96,
            "agent_id": "a-1",
            "user_id": ME,
            "model": "doubao",
        }

    monkeypatch.setattr(mod, "_run_coords", _coords)
    row = DeliverableRow(
        id="42",
        run_id="913",
        kind="script_shot",
        ref_id="9",
        version=4,
        parent_version=3,
        title="S3 · Shot 1 · MS",
    )
    await mod.project_output_best_effort(row, search_text="shot_type: MS\ndesc: rain")
    doc = spy.docs[-1]
    # 身份键是坐标拼出来的，**不是** run_deliverables.id（契约补充）：mig 472 的
    # 回填段按同一个拼法写存量行，两边拼法不一致会让回填行与新写行互不覆盖，
    # 于是同一版在表里有两行而 UNIQUE 拦不住——它比的是这个字符串。
    assert (doc.entity_kind, doc.entity_id) == ("output", "script_shot:9:4")
    assert (doc.kind, doc.ref_id, doc.version, doc.issue_id) == (
        "script_shot",
        "9",
        4,
        96,
    )
    assert doc.body == "shot_type: MS\ndesc: rain"


async def test_a_human_version_owns_itself_and_needs_no_search_text(spy, monkeypatch):
    async def _coords(run_id):
        assert run_id is None
        return None

    monkeypatch.setattr(mod, "_run_coords", _coords)
    row = DeliverableRow(
        id="43",
        run_id=None,
        kind="script_scene",
        ref_id="7",
        version=2,
        parent_version=1,
        title=None,
        actor_user_id=ME,
    )
    await mod.project_output_best_effort(row, search_text=None)
    doc = spy.docs[-1]
    # 新 kind 的生产者忘传正文只是搜不到正文，不是接线 bug（spec §2.1）；
    # 人手版（回退）没有 run，归属只能由署名人给出。
    assert (
        doc.body is None and doc.title == "script_scene #7" and doc.owner_user_id == ME
    )
    # 人手版的身份键与 agent 版同一个拼法——它写在同一条链上，第二种拼法会让
    # 回退产生的那一版在检索里变成一个不同的东西。
    assert doc.entity_id == "script_scene:7:2"


async def test_a_failing_projection_is_logged_and_swallowed(monkeypatch):
    """loguru 不经过 stdlib logging，所以 caplog 看不见它——挂一个自己的 sink。"""
    monkeypatch.setattr(mod, "get_search_docs_repository", lambda: _RepoSpy(boom=True))
    seen: list[str] = []
    sink_id = logger.add(seen.append, level="ERROR", format="{message}")
    try:
        await mod.project_run_best_effort({"id": 916, "issue_id": None, "user_id": ME})
    finally:
        logger.remove(sink_id)
    assert any("search_docs" in line for line in seen), seen
