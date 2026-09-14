"""``GET /api/v1/resources/{resource_id}/provenance`` —— 资源反查产出它的 run。

两件事在这里被钉住：

* **可见性口径与 ``/outputs/{kind}/{ref_id}`` 有意不同一处**。那边议题不可见就
  整条链 404；这边资源 ACL 已经放行了这个文件，再 404 一次等于把用户有权看的
  东西说成不存在。所以链**给**，只把每一版的议题链接按可见性置空——坐标留下，
  按钮禁用（3b spec §5 稿四）。
* **下面断言的是生产错误外壳**。app 装了 ``register_exception_handlers``，所以
  一次拒绝到达客户端时长这样：``{"success": false, "error": …,
  "code": "http_404", "details": {"code": …}}``。裸 ``FastAPI()`` 会给回
  ``{"detail": …}``，那个形状永远不会上线（CLAUDE.md 2026-09-09）。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import register_exception_handlers

mod = importlib.import_module("app.api.resources_provenance_router")

pytestmark = pytest.mark.unit

ME = "11111111-1111-1111-1111-111111111111"
RESOURCE_ID = "912345678901234"
GEN_ID = "337650953731886"
ISSUE_ID = "349426708244346"
ISSUE_KEY = "MH-96"
TEAM_ID = "331438215859255"
RUN_ID = "913402881190401"


def _row(version: int, **over) -> dict:
    """One ``run_deliverables`` row in the shape the repository really returns:
    every id already a string, ``cost_cents`` a float, ``created_at`` an ISO
    string (see ``RunDeliverablesRepository._row``)."""
    row = {
        "id": str(700000000000000 + version),
        "run_id": RUN_ID,
        "seq": version,
        "kind": "generated_media",
        "ref_id": GEN_ID,
        "version": version,
        "parent_version": version - 1 if version > 1 else None,
        "title": f"Cover v{version}",
        "model": "gpt-6-astra",
        "cost_cents": 1.25,
        "turn": 2,
        "step": 3,
        "created_at": f"2026-09-1{version}T00:00:00+00:00",
        "issue_id": ISSUE_ID,
        "issue_key": ISSUE_KEY,
        # Internal to the join: the link builder needs a team, but the team is
        # not part of the version's public shape (see ``lineage_view``).
        "team_id": TEAM_ID,
    }
    row.update(over)
    return row


def _client(
    monkeypatch, *, resource=True, access=True, gen=True, rows=None, visible=None
):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(mod.router, prefix="/api/v1")

    from app.core.deps import get_auth
    from app.core.scope_dep import scoped_request

    app.dependency_overrides[get_auth] = lambda: SimpleNamespace(user_id=ME)
    app.dependency_overrides[scoped_request] = lambda: None
    for dep in mod.router.dependencies:
        app.dependency_overrides[dep.dependency] = lambda: None

    monkeypatch.setattr(
        mod,
        "ResourcesRepository",
        lambda: SimpleNamespace(
            get_resource_by_id=AsyncMock(
                return_value={"id": RESOURCE_ID} if resource else None
            )
        ),
    )
    monkeypatch.setattr(mod, "check_media_access", AsyncMock(return_value=access))
    # The find-or-nothing mock is hoisted out of the factory on purpose: one of
    # the cases below asserts it was NEVER awaited, which a per-call mock could
    # not answer.
    find = AsyncMock(return_value={"id": GEN_ID} if gen else None)
    monkeypatch.setattr(
        mod,
        "GeneratedMediaRepository",
        lambda: SimpleNamespace(find_by_promoted_resource=find),
    )
    repo = SimpleNamespace(
        lineage_for=AsyncMock(return_value=rows if rows is not None else [_row(1)])
    )
    monkeypatch.setattr(mod, "get_run_deliverables_repository", lambda: repo)
    monkeypatch.setattr(
        mod,
        "visible_issue_ids",
        AsyncMock(return_value={ISSUE_ID} if visible is None else visible),
    )
    return TestClient(app), repo, find


def _get(client) -> "object":
    return client.get(f"/api/v1/resources/{RESOURCE_ID}/provenance")


def test_promoted_resource_answers_with_the_generation_chain(monkeypatch):
    client, repo, _find = _client(monkeypatch)
    res = _get(client)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["kind"] == "generated_media"
    assert body["ref_id"] == GEN_ID
    assert body["versions"][0]["issue_key"] == ISSUE_KEY
    # The chain is keyed on the GENERATION, not the resource — the whole point
    # of the reverse lookup. Asserting the kwargs is what catches a swap.
    assert repo.lineage_for.await_args.kwargs == {
        "kind": "generated_media",
        "ref_id": GEN_ID,
    }


def test_a_resource_no_generation_points_at_is_404_not_registered(monkeypatch):
    client, _repo, _find = _client(monkeypatch, gen=False)
    res = _get(client)
    assert res.status_code == 404
    assert res.json()["details"]["code"] == "not_registered"


def test_an_empty_chain_is_also_not_registered(monkeypatch):
    """「有收件箱行但没人登记过」与「没行」对读者是同一件事。"""
    client, _repo, _find = _client(monkeypatch, rows=[])
    res = _get(client)
    assert res.status_code == 404
    assert res.json()["details"]["code"] == "not_registered"


def test_an_invisible_issue_keeps_the_coordinates_and_loses_the_link(monkeypatch):
    """本端点与 ``/outputs/{kind}/{ref_id}`` 唯一的行为差异：那边议题不可见就
    整体 404，这边资源 ACL 已经放行，再 404 等于把「你有权看的文件」说成不
    存在。坐标保留、按钮禁用（spec §5 稿四）。"""
    client, _repo, _find = _client(monkeypatch, visible=set())
    res = _get(client)
    assert res.status_code == 200, res.text
    version = res.json()["versions"][0]
    assert version["issue_id"] == ISSUE_ID
    assert version["issue_key"] is None
    assert version["deep_link"] is None


def test_a_human_version_borrows_the_chains_issue(monkeypatch):
    """回退写的人手版（``run_id IS NULL``）自己答不出「这一版属于哪件工作」——
    它的归属是**这条链的**归属。口径与 ``/outputs/{kind}/{ref_id}`` 必须一致，
    否则同一版在议题页有 issue、在资源面板没有（3b Task 2 fix 轮 1）。

    借来的只有身份：``deep_link`` 仍是 ``None``——链接的锚点是 ``(turn, step)``，
    给人手版一条指向某次运行某一步的 URL 等于声称它是那一步产生的。"""
    human = _row(
        2,
        run_id=None,
        issue_id=None,
        issue_key=None,
        seq=None,
        turn=None,
        step=None,
        actor_user_id=ME,
        reverted_from_version=1,
    )
    client, _repo, _find = _client(monkeypatch, rows=[human, _row(1)])
    res = _get(client)
    assert res.status_code == 200, res.text
    latest = res.json()["versions"][0]
    assert latest["run_id"] is None
    assert latest["issue_id"] == ISSUE_ID
    assert latest["issue_key"] == ISSUE_KEY
    assert latest["deep_link"] is None


def test_a_human_version_with_an_invisible_issue_loses_the_key_too(monkeypatch):
    """借来的身份不绕过可见性：坐标留下，链接置空。"""
    human = _row(2, run_id=None, issue_id=None, issue_key=None, seq=None)
    client, _repo, _find = _client(monkeypatch, rows=[human, _row(1)], visible=set())
    latest = _get(client).json()["versions"][0]
    assert latest["issue_id"] == ISSUE_ID
    assert latest["issue_key"] is None
    assert latest["deep_link"] is None


def test_a_resource_the_caller_cannot_access_is_403(monkeypatch):
    client, _repo, find = _client(monkeypatch, access=False)
    res = _get(client)
    assert res.status_code == 403
    assert res.json()["details"]["code"] == "forbidden"
    # 守卫在解析之前：一次被拒绝的读不该先去数据库问「这是谁做的」。
    find.assert_not_awaited()


def test_a_missing_resource_is_404_not_found(monkeypatch):
    client, _repo, _find = _client(monkeypatch, resource=False)
    res = _get(client)
    assert res.status_code == 404
    assert res.json()["details"]["code"] == "not_found"


def test_ids_stay_strings_on_the_wire(monkeypatch):
    """Snowflake BIGINT 过 2^53 在浏览器里会掉精度（CLAUDE.md 已知陷阱）。

    喂 string id 然后断言 string 是同义反复——它对「schema 上那些 ``str``
    标注是不是真的在干活」一个字都没说。所以这里喂 **int** id：``OutputVersion``
    必须拒绝它，而不是把一个 JSON number 发给浏览器。pydantic v2 的宽松模式
    **不**做 int→str 强转，这正是我们要钉住的那件事——把 ``id: str`` 放宽成
    ``Any`` 或 ``int``，这条就会转绿（而线上会开始掉精度）。
    """
    from pydantic import ValidationError

    client, _repo, _find = _client(
        monkeypatch,
        rows=[_row(1, id=700000000000001, run_id=913402881190401)],
    )
    with pytest.raises(ValidationError) as caught:
        _get(client)
    bad = {err["loc"][-1] for err in caught.value.errors()}
    assert {"id", "run_id"} <= bad, caught.value.errors()

    # 正向对照：仓库真实返回的形状（id 已是 string）照常过关并保持 string。
    client, _repo, _find = _client(monkeypatch)
    version = _get(client).json()["versions"][0]
    assert isinstance(version["run_id"], str)
    assert isinstance(version["id"], str)


def test_as_of_seq_is_the_newest_registration_id_as_a_string(monkeypatch):
    """水位 = 链上最大的 ``run_deliverables.id``，**字符串**。

    断言的是路由**算出来的 kwargs**，不是响应体里的键：``as_of_seq`` 是 T4 的
    字段，T4 未合并时 pydantic 的 ``extra="ignore"`` 会把它丢掉，去读 JSON 的
    测试在今天必然看不到它——那样这一行就完全无人把守，直到 T4 合并才第一次
    被检验。

    三件事各自可证伪：
    * 不是 ``seq``（这里 seq 是 1/2/3，id 是 7000000000000 0x，值不同）；
    * 不是 ``rows[0]``（行按 2/3/1 乱序喂进去，答案仍是 v3 那行）；
    * 不是 ``int``（Snowflake 过 2^53 掉精度）。
    """
    real = mod.OutputLineageResponse
    seen: dict = {}

    def _spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    client, _repo, _find = _client(monkeypatch, rows=[_row(2), _row(3), _row(1)])
    monkeypatch.setattr(mod, "OutputLineageResponse", _spy)
    assert _get(client).status_code == 200

    assert seen["as_of_seq"] == "700000000000003"
    assert isinstance(seen["as_of_seq"], str)
