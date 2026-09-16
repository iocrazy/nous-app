"""``GET /api/v1/outputs/{kind}/{ref_id}`` (+ ``/diff``) — one object's lineage.

Two disciplines are pinned here on purpose:

* **Not registered is a 404 with a typed code, never an empty list.** "Nobody
  registered this object" and "this object has no versions yet" are different
  answers; an empty array makes the UI draw an empty provenance block where the
  honest answer is that the object is not in the registry at all (spec §4).
* **The bodies below are the PRODUCTION error envelope.** The app under test
  installs ``register_exception_handlers``, so a refusal arrives as
  ``{"success": false, "error": …, "code": "http_404", "details": {"code": …}}``.
  A bare ``FastAPI()`` would hand back ``{"detail": …}`` and every assertion
  here would pass against a shape that never ships (CLAUDE.md 2026-09-09).
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.exceptions import register_exception_handlers

mod = importlib.import_module("app.api.outputs_router")

pytestmark = pytest.mark.unit

ME = "11111111-1111-1111-1111-111111111111"
SOMEONE_ELSE = "22222222-2222-2222-2222-222222222222"
ISSUE_ID = "348087075560200"
ISSUE_KEY = "MH-91"
TEAM_ID = "424242424242"
RUN_ID = "913402881190401"


def _row(version: int, **over) -> dict:
    """One ``run_deliverables`` row in the shape the repository really returns:
    every id already a string, ``cost_cents`` a float, ``created_at`` an ISO
    string (see ``RunDeliverablesRepository._row``)."""
    row = {
        "id": str(700000000000000 + version),
        "run_id": RUN_ID,
        "seq": version,
        "kind": "script_shot",
        "ref_id": "9",
        "version": version,
        "parent_version": version - 1 if version > 1 else None,
        "title": f"S1 · Shot {version}",
        "model": "qwen-max",
        # 文本类登记行的 ``cost_cents`` 永远是 NULL（3b：不回写，读时才按步
        # 分摊）。写 1.25 会让整个文件在一个生产里不存在的形状上跑绿。
        "cost_cents": None,
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


SEEDED = [_row(3), _row(2), _row(1)]  # repository order: newest version first


class _Repo:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple] = []

    async def lineage_for(self, *, kind, ref_id):
        self.calls.append((kind, str(ref_id)))
        return [
            r for r in self.rows if r["kind"] == kind and r["ref_id"] == str(ref_id)
        ]


class _IssueGate:
    """Stub for ``visible_issue_ids`` — the per-issue batch check.

    ``allowed=None`` means "every issue in the batch is visible", which is what
    the tests that are not about redaction want. Every call is recorded — the
    id set AND the auth it was asked on behalf of — so the "one round trip,
    deduplicated, for THIS caller" claim can be pinned rather than assumed.
    """

    def __init__(self, allowed=None):
        self.allowed = allowed
        self.calls: list[set] = []
        self.auths: list = []

    async def __call__(self, issue_ids, auth):
        asked = {str(i) for i in issue_ids if i is not None}
        self.calls.append(asked)
        self.auths.append(auth)
        if self.allowed is None:
            return asked
        return {i for i in asked if i in self.allowed}


class _Citations:
    """Stub for ``OutputCitationsRepository`` (3c §2.2).

    ``counts`` is the FULL count per version; ``cited`` the rows a caller might
    be shown. Every call is recorded so "one GROUP BY for the whole chain" is a
    pinned fact rather than an assumption.
    """

    def __init__(self, counts=None, cited=None, calls=None):
        self.counts = counts if counts is not None else {}
        self.cited = cited if cited is not None else {}
        self.calls = calls if calls is not None else {"count": 0, "list": []}

    async def counts_for_chain(self, kind, ref_id):
        self.calls["count"] += 1
        return dict(self.counts)

    async def list_for_ref(self, kind, ref_id, version):
        self.calls["list"].append(int(version))
        return list(self.cited.get(int(version), []))


def _client(monkeypatch, rows=SEEDED, *, visible=True, owner=ME, diff=None, gate=None):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(mod.router, prefix="/api/v1")

    from app.core.deps import get_auth

    async def _grant():
        return SimpleNamespace(user_id=ME)

    app.dependency_overrides[get_auth] = _grant
    for dep in mod.router.dependencies:
        app.dependency_overrides[dep.dependency] = lambda: None

    repo = _Repo(rows)
    monkeypatch.setattr(mod, "get_run_deliverables_repository", lambda: repo)
    monkeypatch.setattr(
        mod,
        "assert_issue_visible",
        (
            AsyncMock(return_value={"id": int(ISSUE_ID)})
            if visible
            else AsyncMock(side_effect=HTTPException(404, "not found"))
        ),
    )
    monkeypatch.setattr(mod, "run_owner_user_id", AsyncMock(return_value=owner))
    monkeypatch.setattr(
        mod, "visible_issue_ids", gate if gate is not None else _IssueGate()
    )
    monkeypatch.setattr(
        mod, "build_diff", AsyncMock(return_value=diff if diff is not None else {})
    )
    # 读时分摊默认「没有份额可分」：不打桩的话每个用例都会真的去读事件流
    # （``load_step_shares`` 自己吞异常，于是失败会静默成 {} 而不是报错）。
    # 关心分摊的用例在 ``_client`` 之后自己覆盖它。
    monkeypatch.setattr(mod, "load_step_shares", AsyncMock(return_value={}))
    # 引用反查默认「这条链一次都没被引用过」：不打桩的话每个用例都会真的去读
    # ``output_citations``（3c §2.2）。关心引用的用例走 ``lineage_wired``。
    monkeypatch.setattr(mod, "get_output_citations_repository", lambda: _Citations())
    return TestClient(app)


# ── lineage ──────────────────────────────────────────────────────────────


def test_lineage_returns_versions_newest_first(monkeypatch):
    r = _client(monkeypatch).get("/api/v1/outputs/script_shot/9")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [v["version"] for v in body["versions"]] == [3, 2, 1]
    assert body["latest_version"] == 3
    assert body["kind"] == "script_shot" and body["ref_id"] == "9"


def test_lineage_ids_stay_strings_on_the_wire(monkeypatch):
    """Snowflake BIGINTs lose precision above 2^53 once JS parses them as
    numbers, so every id leaves as a string — ``issue_id`` included."""
    body = _client(monkeypatch).get("/api/v1/outputs/script_shot/9").json()
    top = body["versions"][0]
    assert top["issue_id"] == ISSUE_ID
    assert isinstance(top["issue_id"], str)
    assert isinstance(top["run_id"], str) and top["run_id"] == RUN_ID
    assert isinstance(top["id"], str)


def test_lineage_carries_the_lineage_columns(monkeypatch):
    top = (
        _client(monkeypatch).get("/api/v1/outputs/script_shot/9").json()["versions"][0]
    )
    assert top["parent_version"] == 2
    assert top["title"] == "S1 · Shot 3"
    assert top["model"] == "qwen-max"
    # 登记行没有花费；本用例没有 step_end 份额，所以两个键都是 None（分摊本身
    # 由 test_lineage_allocates_the_step_cost_and_reports_the_kind 钉住）。
    assert top["cost_cents"] is None and top["cost_kind"] is None
    assert top["turn"] == 2 and top["step"] == 3


def test_lineage_carries_the_issue_key_and_a_deep_link(monkeypatch):
    """3a Task 3b: ``issue_id`` alone is unusable — the issue route is keyed by
    the identifier inside a team, so the panel could only ever draw a disabled
    button. The endpoint now says WHICH issue and WHERE it lives."""
    top = (
        _client(monkeypatch).get("/api/v1/outputs/script_shot/9").json()["versions"][0]
    )
    assert top["issue_key"] == ISSUE_KEY
    assert top["deep_link"] == f"/team/{TEAM_ID}/todolist/{ISSUE_KEY}?step=3&turn=2"


def test_the_step_anchor_is_part_of_the_link(monkeypatch):
    """A run of thirty steps opens on the step that produced THIS version, not
    at the top of the issue. The turn rides along: the trajectory keys its
    nodes by ``(turn, step)``, so a multi-turn run has several step 7s."""
    rows = [_row(1, step=7, turn=4)]
    body = _client(monkeypatch, rows).get("/api/v1/outputs/script_shot/9").json()
    assert body["versions"][0]["deep_link"].endswith("?step=7&turn=4")


def test_a_version_with_no_turn_keeps_the_one_key_anchor(monkeypatch):
    """Rows registered before ``turn`` was recorded. The link is exactly what
    it was — never ``&turn=None``."""
    rows = [_row(1, step=7, turn=None)]
    body = _client(monkeypatch, rows).get("/api/v1/outputs/script_shot/9").json()
    assert body["versions"][0]["deep_link"].endswith("?step=7")


def test_a_version_with_no_step_links_to_the_issue_without_an_anchor(monkeypatch):
    rows = [_row(1, step=None)]
    body = _client(monkeypatch, rows).get("/api/v1/outputs/script_shot/9").json()
    assert body["versions"][0]["deep_link"] == f"/team/{TEAM_ID}/todolist/{ISSUE_KEY}"


def test_a_run_with_no_issue_has_neither_key_nor_link(monkeypatch):
    """A canvas or chat lane run answers to no issue. Both fields are None —
    never a URL assembled from a run id."""
    rows = [_row(1, issue_id=None, issue_key=None, team_id=None)]
    top = (
        _client(monkeypatch, rows, owner=ME)
        .get("/api/v1/outputs/script_shot/9")
        .json()["versions"][0]
    )
    assert top["issue_id"] is None
    assert top["issue_key"] is None and top["deep_link"] is None


def test_an_issue_without_an_identifier_gets_no_link(monkeypatch):
    """The id is not a substitute for the key: ``/todolist/348087075560200``
    resolves to nothing, and a dead link reads worse than a disabled button."""
    rows = [_row(1, issue_key=None)]
    top = (
        _client(monkeypatch, rows)
        .get("/api/v1/outputs/script_shot/9")
        .json()["versions"][0]
    )
    assert top["issue_id"] == ISSUE_ID
    assert top["issue_key"] is None and top["deep_link"] is None


def test_an_issue_with_no_team_keeps_the_key_but_builds_no_link(monkeypatch):
    """A personal-scope issue has ``team_id IS NULL``. The key is still a fact
    worth printing; the URL is not buildable, so it stays None."""
    rows = [_row(1, team_id=None)]
    top = (
        _client(monkeypatch, rows)
        .get("/api/v1/outputs/script_shot/9")
        .json()["versions"][0]
    )
    assert top["issue_key"] == ISSUE_KEY
    assert top["deep_link"] is None


def test_team_id_never_reaches_the_wire(monkeypatch):
    """It feeds the link builder and nothing else — the version's public shape
    is an explicit projection, not ``dict(row)``."""
    top = (
        _client(monkeypatch).get("/api/v1/outputs/script_shot/9").json()["versions"][0]
    )
    assert "team_id" not in top


def test_the_lineage_link_is_byte_identical_to_the_generated_inbox_one(monkeypatch):
    """ONE builder. The Generated card and the lineage panel describe the same
    row, so they must print the same string — a second builder would drift by a
    query string and nothing would fail."""
    from app.services.library.generated_source import describe_source

    row = _row(3)
    top = (
        _client(monkeypatch).get("/api/v1/outputs/script_shot/9").json()["versions"][0]
    )
    card = describe_source(
        {"origin_kind": "agent_run", "id": "500"},
        canvas_names={},
        team_id=row["team_id"],
        provenance={
            "run_id": row["run_id"],
            "issue_id": row["issue_id"],
            "issue_key": row["issue_key"],
            "agent_name": "Script Ai",
            "step": row["step"],
            "turn": row["turn"],
        },
    )
    assert top["deep_link"] == card["deep_link"]


def test_unregistered_object_is_404_not_empty(monkeypatch):
    """「没登记」与「没产出」是两件事：空数组会让 UI 画一个空的来源块，
    而正确的回答是这个对象根本不在登记表里。"""
    r = _client(monkeypatch).get("/api/v1/outputs/script_shot/999999")
    assert r.status_code == 404
    assert r.json()["details"]["code"] == "not_registered"


def test_an_unknown_kind_is_a_typed_400(monkeypatch):
    r = _client(monkeypatch).get("/api/v1/outputs/script_haiku/9")
    assert r.status_code == 400
    assert r.json()["details"]["code"] == "unknown_kind"


def test_lineage_of_an_invisible_issue_is_404(monkeypatch):
    r = _client(monkeypatch, visible=False).get("/api/v1/outputs/script_shot/9")
    assert r.status_code == 404


def test_a_run_with_no_issue_is_visible_only_to_its_own_user(monkeypatch):
    # No issue means no JOIN, so the key and the team are absent too — the
    # three arrive together or not at all (边界 mock 必须用真实 JSON 形状).
    rows = [_row(1, issue_id=None, issue_key=None, team_id=None)]
    assert (
        _client(monkeypatch, rows, owner=ME)
        .get("/api/v1/outputs/script_shot/9")
        .status_code
        == 200
    )
    assert (
        _client(monkeypatch, rows, owner=SOMEONE_ELSE)
        .get("/api/v1/outputs/script_shot/9")
        .status_code
        == 404
    )


def test_lineage_allocates_the_step_cost_and_reports_the_kind(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        mod, "load_step_shares", AsyncMock(return_value={(int(RUN_ID), 2, 3): 0.09})
    )
    body = client.get("/api/v1/outputs/script_shot/9").json()
    assert [(v["cost_cents"], v["cost_kind"]) for v in body["versions"]] == [
        (0.09, "allocated")
    ] * 3


def test_a_media_versions_registered_cost_stays_exact(monkeypatch):
    """媒体类登记时就有真价（目录每次调用价）——读时不许拿份额把它盖掉。"""
    client = _client(monkeypatch, rows=[_row(1, cost_cents=12.0)])
    monkeypatch.setattr(
        mod, "load_step_shares", AsyncMock(return_value={(int(RUN_ID), 2, 3): 0.09})
    )
    top = client.get("/api/v1/outputs/script_shot/9").json()["versions"][0]
    assert (top["cost_cents"], top["cost_kind"]) == (12.0, "exact")


def test_lineage_reports_the_chain_watermark(monkeypatch):
    """``as_of_seq`` = 这条链上**最大的登记行 id**（Snowflake，单调）。

    刻意**不是** transcript ``seq``：seq 是每个 run 内部的小整数，人手版根本
    没有，两把尺子混在一个字段里会给出一个会变小的水位（见下一条）。"""
    client = _client(monkeypatch)
    mark = client.get("/api/v1/outputs/script_shot/9").json()["as_of_seq"]
    # Snowflake 超过 2^53，JSON number 一进浏览器就掉精度（CLAUDE.md BIGINT
    # 陷阱），所以它以**字符串**出口，前端用 BigInt 比大小。
    assert mark == "700000000000003" and isinstance(mark, str)


def test_the_watermark_only_grows_across_a_revert(monkeypatch):
    """回退是唯一会让两种版本交替出现在链首的场景：v3(agent) → v4(人手) →
    v5(agent)。水位必须严格递增——客户端只拿它拒绝「用更旧的响应盖掉更新的」。

    这一条钉的正是「按 seq 取水位」会错的地方：那样 v5 的水位是它的 seq(=5)，
    比 v4 的行 id（Snowflake，7e14 量级）小，于是最新的一次响应会被当过期丢掉。
    """
    human = _row(4, run_id=None, issue_id=None, issue_key=None, team_id=None, seq=None)
    states = [
        [_row(3), _row(2), _row(1)],
        [human, _row(3), _row(2), _row(1)],
        [_row(5, seq=5), human, _row(3), _row(2), _row(1)],
    ]
    marks = [
        _client(monkeypatch, rows=rows)
        .get("/api/v1/outputs/script_shot/9")
        .json()["as_of_seq"]
        for rows in states
    ]
    assert marks == ["700000000000003", "700000000000004", "700000000000005"]
    assert all(isinstance(m, str) for m in marks)
    # 严格递增。用 int 比，而不是靠等长字符串的字典序碰巧成立——客户端那边是
    # BigInt，这里就按数值比。
    numbers = [int(m) for m in marks]
    assert numbers == sorted(numbers) and len(set(numbers)) == len(numbers)


def test_the_watermark_is_the_max_not_the_first_row(monkeypatch):
    """仓库按 version DESC 排，但「版本号最大」与「行 id 最大」不是同一件事：
    一条落后的链上重新登记一个旧版本号，行 id 仍然更大。取 max 而不是取首行。"""
    client = _client(monkeypatch, rows=[_row(2), _row(9)])
    assert client.get("/api/v1/outputs/script_shot/9").json()["as_of_seq"] == (
        "700000000000009"
    )


def test_a_version_with_no_run_asks_for_no_share(monkeypatch):
    """人手版没有 run，不该出现在装载的 run 清单里（``int(None)`` 当场炸）。"""
    asked: list[list] = []

    async def _shares(run_ids):
        asked.append(list(run_ids))
        return {}

    client = _client(
        monkeypatch,
        rows=[
            _row(2, run_id=None, issue_id=None, issue_key=None, team_id=None, seq=None),
            _row(1),
        ],
    )
    monkeypatch.setattr(mod, "load_step_shares", _shares)
    assert client.get("/api/v1/outputs/script_shot/9").status_code == 200
    assert asked == [[int(RUN_ID)]]


def test_a_human_revert_version_is_returned_and_does_not_decide_the_gate(monkeypatch):
    """3b：回退版没有 run，也没有 issue。它必须出现在链上（否则面板上最新的
    那一版凭空消失），而门禁必须落在最新的**有 run 的**那一版上——拿人手版
    当判据会把 ``run_owner_user_id`` 喂成 None。"""
    rows = [
        _row(
            4,
            run_id=None,
            actor_user_id=ME,
            reverted_from_version=1,
            issue_id=None,
            issue_key=None,
            team_id=None,
            seq=None,
            turn=None,
            step=None,
            model=None,
            cost_cents=None,
        ),
        _row(3),
        _row(2),
        _row(1),
    ]
    body = _client(monkeypatch, rows).get("/api/v1/outputs/script_shot/9").json()
    assert [v["version"] for v in body["versions"]] == [4, 3, 2, 1]
    human = body["versions"][0]
    assert human["run_id"] is None
    assert human["actor_user_id"] == ME
    assert human["reverted_from_version"] == 1
    # 门禁**真的**问了 v3 的 issue。断言这一句而不是断言 200：人手版走另一条
    # 分支（run 属主）时照样能拿到 200，那样这个用例就证明不了任何事。
    mod.assert_issue_visible.assert_awaited_once()
    assert mod.assert_issue_visible.await_args.args[0] == int(ISSUE_ID)


def test_a_lineage_human_row_carries_the_chains_issue_and_no_deep_link(monkeypatch):
    """3b ruling 3，血缘那一侧：人手版自己没有 issue（``lineage_for`` 的 outer
    join 给的是 NULL），但它长在这条链上，所以要报出**这条链的**
    ``issue_id`` / ``issue_key``；``deep_link`` 与 ``turn`` / ``step`` 一律 None
    —— 锚点属于某次运行的某一步，人手版没有那一步。"""
    # ⚠️ 这一行**故意**带着 turn / step（2 / 3）。真实的回退行不该有，但「不该有」
    # 不是防护：投影必须自己清掉它们，否则 issue_deep_link 会照样拼出一条锚到
    # 别人那一步的 URL。留着它，这个用例才对 version_of 的人手分支可证伪。
    rows = [
        _row(
            4,
            run_id=None,
            actor_user_id=ME,
            reverted_from_version=1,
            issue_id=None,
            issue_key=None,
            team_id=None,
            seq=None,
        ),
        _row(3),
    ]
    body = _client(monkeypatch, rows).get("/api/v1/outputs/script_shot/9").json()
    human = body["versions"][0]
    assert human["issue_id"] == ISSUE_ID
    assert human["issue_key"] == ISSUE_KEY
    assert human["deep_link"] is None
    assert human["turn"] is None and human["step"] is None
    # 正向对照：门禁那一版照常拿到链接。
    assert body["versions"][1]["deep_link"].endswith(
        f"/todolist/{ISSUE_KEY}?step=3&turn=2"
    )


def test_the_chains_issue_is_lent_to_human_rows_only(monkeypatch):
    """反向对照：一条画布道 run 的旧版本（有 run、没有 issue）**不**该因为这次
    补写而被说成属于这条链的 issue。只有人手版借身份。"""
    rows = [
        _row(
            3,
            run_id=None,
            actor_user_id=ME,
            issue_id=None,
            issue_key=None,
            team_id=None,
        ),
        _row(2),
        _row(1, issue_id=None, issue_key=None, team_id=None),  # 画布道 run
    ]
    body = _client(monkeypatch, rows).get("/api/v1/outputs/script_shot/9").json()
    assert body["versions"][0]["issue_id"] == ISSUE_ID  # 人手版：借
    assert body["versions"][2]["issue_id"] is None  # 画布道 run：不借
    assert body["versions"][2]["deep_link"] is None


def test_a_human_revert_is_refused_when_the_gating_run_is_invisible(monkeypatch):
    """反向对照：把门禁那一版的 issue 设成不可见，回退版也跟着 404 —— 证明
    上面那次 200 是门禁放行的结果，不是门禁被人手版绕过了。"""
    rows = [
        _row(4, run_id=None, actor_user_id=ME, issue_id=None, team_id=None),
        _row(3),
    ]
    r = _client(monkeypatch, rows, visible=False).get("/api/v1/outputs/script_shot/9")
    assert r.status_code == 404


def test_a_chain_with_no_run_at_all_is_404_not_a_crash(monkeypatch):
    """没有任何一版能证明归属时，回答与「没登记过」一致。"""
    rows = [_row(1, run_id=None, actor_user_id=SOMEONE_ELSE, issue_id=None)]
    r = _client(monkeypatch, rows).get("/api/v1/outputs/script_shot/9")
    assert r.status_code == 404
    # 而且**没有**去问一个 NULL run 的属主：真栈上 ``int(None)`` 是 500。
    mod.run_owner_user_id.assert_not_awaited()


# ── diff ─────────────────────────────────────────────────────────────────


def test_diff_rejects_a_version_that_does_not_exist(monkeypatch):
    r = _client(monkeypatch).get("/api/v1/outputs/script_shot/9/diff?from=1&to=7")
    assert r.status_code == 404
    assert r.json()["details"]["code"] == "version_not_found"


def test_diff_of_an_unregistered_object_is_not_registered(monkeypatch):
    r = _client(monkeypatch).get("/api/v1/outputs/script_shot/404/diff?from=1&to=2")
    assert r.status_code == 404
    assert r.json()["details"]["code"] == "not_registered"


def _diff_body() -> dict:
    """What ``build_diff`` returns, in the shape ``OutputDiffResponse`` validates."""
    side = {
        "version": 1,
        "run_id": RUN_ID,
        "issue_id": ISSUE_ID,
        "created_at": "2026-09-11T00:00:00+00:00",
        "model": "qwen-max",
        "cost_cents": 1.25,
        "title": "S1 · Shot 1",
        "text": "description: a wide shot",
        "media": None,
        "available": True,
        "unavailable_reason": None,
    }
    return {
        "kind": "script_shot",
        "ref_id": "9",
        "content_type": "text",
        "from": side,
        "to": {**side, "version": 2, "text": "description: a close-up"},
    }


def test_diff_returns_two_sides_keyed_from_and_to(monkeypatch):
    diff = _diff_body()
    r = _client(monkeypatch, diff=diff).get(
        "/api/v1/outputs/script_shot/9/diff?from=1&to=2"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content_type"] == "text"
    assert body["from"]["version"] == 1 and body["to"]["version"] == 2
    assert body["from"]["text"] == "description: a wide shot"
    assert body["to"]["issue_id"] == ISSUE_ID


def test_diff_lends_the_chains_issue_to_a_human_version(monkeypatch):
    """回退写的人手版 ``run_id IS NULL`` ⇒ 它自己的 ``issue_id`` 也是 NULL。

    回退响应与血缘端点都用 ``newest_with_a_run`` 把这条链的归属补给它（3b fix
    轮 1）；diff 是第三个读者，不补的话同一版在面板上一会儿有归属一会儿没有
    （Task 9 旁证 C）。"""
    human = _row(4, run_id=None, issue_id=None, issue_key=None, turn=None, step=None)
    rows = [human, _row(3), _row(2), _row(1)]
    diff = _diff_body()
    r = _client(monkeypatch, rows=rows, diff=diff).get(
        "/api/v1/outputs/script_shot/9/diff?from=3&to=4"
    )
    assert r.status_code == 200, r.text
    passed = mod.build_diff.await_args.kwargs
    assert passed["to_row"]["issue_id"] == ISSUE_ID
    assert passed["to_row"]["issue_key"] == ISSUE_KEY
    # agent 版原样传递：它自己答得出归属，补给它等于把别的 issue 的版本说成
    # 这条链的（3b fix 轮 1 的那条纪律）。
    assert passed["from_row"]["issue_id"] == ISSUE_ID
    assert passed["from_row"]["run_id"] == RUN_ID


def test_diff_checks_visibility_before_reading_content(monkeypatch):
    c = _client(monkeypatch, visible=False)
    assert c.get("/api/v1/outputs/script_shot/9/diff?from=1&to=2").status_code == 404
    mod.build_diff.assert_not_awaited()


# ── per-object gate: one issue's visibility is not every issue's ─────────


OTHER_ISSUE_ID = "348087075560999"
OTHER_ISSUE_KEY = "OPS-3"
OTHER_TEAM_ID = "999999999999"


def _cross_issue_rows():
    """A two-version chain whose older version was filed under another issue,
    in another team — the shape the redaction exists for."""
    return [
        _row(2),
        _row(
            1,
            issue_id=OTHER_ISSUE_ID,
            issue_key=OTHER_ISSUE_KEY,
            team_id=OTHER_TEAM_ID,
        ),
    ]


def test_an_older_version_on_another_issue_loses_its_key_and_link(monkeypatch):
    """The router's gate proves the NEWEST version's issue, but every row
    builds its OWN ``issue_key`` / ``deep_link`` out of its OWN team. When the
    batch check says that other issue is invisible, nothing about it may reach
    the wire — not the key, not the team inside the URL (3a Task 8b / A1)."""
    gate = _IssueGate(allowed={ISSUE_ID})
    body = (
        _client(monkeypatch, _cross_issue_rows(), gate=gate)
        .get("/api/v1/outputs/script_shot/9")
        .json()
    )
    newest, older = body["versions"]
    assert newest["issue_key"] == ISSUE_KEY
    assert newest["deep_link"] == f"/team/{TEAM_ID}/todolist/{ISSUE_KEY}?step=3&turn=2"
    assert older["issue_key"] is None
    assert older["deep_link"] is None
    wire = json.dumps(body)
    assert OTHER_ISSUE_KEY not in wire
    assert OTHER_TEAM_ID not in wire


def test_a_visible_sibling_issue_keeps_its_link(monkeypatch):
    """A1: the old rule blanked EVERY foreign issue, so a sibling issue in the
    caller's own team lost its link too — a correct link, thrown away because
    nobody had asked. Asked and answered "yes", the link stays."""
    gate = _IssueGate(allowed={ISSUE_ID, OTHER_ISSUE_ID})
    body = (
        _client(monkeypatch, _cross_issue_rows(), gate=gate)
        .get("/api/v1/outputs/script_shot/9")
        .json()
    )
    newest, older = body["versions"]
    assert newest["deep_link"] == f"/team/{TEAM_ID}/todolist/{ISSUE_KEY}?step=3&turn=2"
    assert older["issue_key"] == OTHER_ISSUE_KEY
    assert (
        older["deep_link"]
        == f"/team/{OTHER_TEAM_ID}/todolist/{OTHER_ISSUE_KEY}?step=3&turn=2"
    )


def test_an_invisible_older_issue_loses_its_link(monkeypatch):
    """Answered "no": the link goes, the coordinate stays. Task 3b ruled the
    bare snowflake is a coordinate and not a route, so ``issue_id`` survives on
    both versions — blanking it would be a different (and wider) decision."""
    gate = _IssueGate(allowed={ISSUE_ID})
    body = (
        _client(monkeypatch, _cross_issue_rows(), gate=gate)
        .get("/api/v1/outputs/script_shot/9")
        .json()
    )
    newest, older = body["versions"]
    assert older["issue_key"] is None and older["deep_link"] is None
    assert newest["issue_key"] == ISSUE_KEY and newest["deep_link"]
    assert [v["issue_id"] for v in body["versions"]] == [ISSUE_ID, OTHER_ISSUE_ID]


def test_every_version_of_the_gated_issue_keeps_its_link(monkeypatch):
    """A chain that never leaves one visible issue is untouched, so the panel
    still links every revision."""
    gate = _IssueGate(allowed={ISSUE_ID})
    body = _client(monkeypatch, gate=gate).get("/api/v1/outputs/script_shot/9").json()
    assert [v["issue_key"] for v in body["versions"]] == [ISSUE_KEY] * 3
    assert all(v["deep_link"] for v in body["versions"])


def test_a_no_issue_chain_redacts_an_older_version_that_has_one(monkeypatch):
    """The newest version answers to no issue, so the entry gate was the RUN's
    owner. The older version's issue is still put to the batch check — and when
    that says no, its link goes."""
    rows = [
        _row(2, issue_id=None, issue_key=None, team_id=None),
        _row(1),
    ]
    gate = _IssueGate(allowed=set())
    body = (
        _client(monkeypatch, rows, owner=ME, gate=gate)
        .get("/api/v1/outputs/script_shot/9")
        .json()
    )
    older = body["versions"][1]
    assert older["issue_id"] == ISSUE_ID
    assert older["issue_key"] is None and older["deep_link"] is None
    assert gate.calls == [{ISSUE_ID}]


def test_visibility_is_checked_once_per_distinct_issue(monkeypatch):
    """The cost model this replaces the blanket rule with: ONE batch call for
    the whole chain, asked about the DEDUPLICATED set of issues — not one round
    trip per version, which a long chain would turn into a fan-out."""
    rows = [_row(v) for v in (6, 5, 4)] + [
        _row(v, issue_id=OTHER_ISSUE_ID, issue_key=OTHER_ISSUE_KEY) for v in (3, 2, 1)
    ]
    gate = _IssueGate()
    _client(monkeypatch, rows, gate=gate).get("/api/v1/outputs/script_shot/9")
    assert gate.calls == [{ISSUE_ID, OTHER_ISSUE_ID}]
    # Asked on behalf of THIS caller. Visibility is a per-user fact, so a
    # router that dropped the auth (or passed None) must not pass here.
    assert [a.user_id for a in gate.auths] == [ME]


def test_diff_asks_about_its_two_sides_and_nothing_else(monkeypatch):
    """``/diff`` 也要买那次批量可见性问答 —— 但只问它真正要画的两侧。

    （这条替换了「diff 不该付这笔查询」的旧断言：3b fix A 给两侧加上
    ``issue_key`` 之后，「没有链接要裁」的前提就不成立了。）"""
    gate = _IssueGate()
    rows = [_row(3), _row(2), _row(1, issue_id=OTHER_ISSUE_ID)]
    r = _client(monkeypatch, rows=rows, gate=gate, diff=_diff_body()).get(
        "/api/v1/outputs/script_shot/9/diff?from=2&to=3"
    )
    assert r.status_code == 200, r.text
    # 一次调用，只含这两侧的 issue（v1 那件外来 issue 不在问答里 —— 它不被画）。
    assert gate.calls == [{ISSUE_ID}]
    assert [a.user_id for a in gate.auths] == [ME]


def test_diff_redacts_a_side_whose_issue_the_caller_cannot_see(monkeypatch):
    """进门那道闸只证明了「最新的有 run 的那一版」的 issue 可见。另一侧可以是
    **另一件** issue 下的旧版本，而 ``issue_key`` 是可路由的 —— 不裁就是跨团队
    边界一行一行地漏（3a Task 8b / 小票 A1 的 diff 版）。

    借来的那一侧（人手版，带的是刚被判过的那件 issue）必须活下来，否则这次裁剪
    就把 Task 9 旁证 C 刚修好的东西又拿走了。"""
    human = _row(4, run_id=None, issue_id=None, issue_key=None, turn=None, step=None)
    foreign = _row(1, issue_id=OTHER_ISSUE_ID, issue_key=OTHER_ISSUE_KEY)
    rows = [human, _row(3), _row(2), foreign]
    gate = _IssueGate(allowed={ISSUE_ID})  # 外来那件不可见
    r = _client(monkeypatch, rows=rows, gate=gate, diff=_diff_body()).get(
        "/api/v1/outputs/script_shot/9/diff?from=1&to=4"
    )
    assert r.status_code == 200, r.text
    passed = mod.build_diff.await_args.kwargs
    # 外来那一侧：坐标（issue_id）留着，可路由的两个字段清掉。
    assert passed["from_row"]["issue_id"] == OTHER_ISSUE_ID
    assert passed["from_row"]["issue_key"] is None
    assert passed["from_row"]["deep_link"] is None
    # 人手那一侧：借到的正是被判过的那件 issue，照常活下来。
    assert passed["to_row"]["issue_id"] == ISSUE_ID
    assert passed["to_row"]["issue_key"] == ISSUE_KEY


# ── 引用反查：cited_count / cited_in（3c §2.2） ───────────────────────────


_AT = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
CITING_ISSUE = "96"
FOREIGN_CITING_ISSUE = "999"


class _Auth:
    user_id = ME


@pytest.fixture
def lineage_wired(monkeypatch):
    """血缘端点的四个外部面，一次装好。

    直接调端点函数（不经 TestClient）是刻意的：这三条断言的是**发了几次查询**
    和**裁掉了谁**，而不是错误信封的形状——文件顶部那条纪律管的是拒绝路径。
    """
    state = {
        "rows": SEEDED,
        #: 全量计数（{version: n}）。
        "counts": {},
        #: {version: [引用行]}，``list_for_ref`` 的返回形状。
        "cited": {},
        #: None = 每件议题都可见；给一个集合就只有集合里的可见。
        "visible": None,
        "keys": {CITING_ISSUE: "MH-96", FOREIGN_CITING_ISSUE: "MH-999"},
        "count_calls": 0,
        "list_calls": [],
    }

    class _Repo:
        async def counts_for_chain(self, kind, ref_id):
            state["count_calls"] += 1
            return dict(state["counts"])

        async def list_for_ref(self, kind, ref_id, version):
            state["list_calls"].append(int(version))
            return list(state["cited"].get(int(version), []))

    async def _chain(kind, ref_id, auth):
        return state["rows"]

    async def _visible(issue_ids, auth):
        asked = {str(i) for i in issue_ids if i is not None}
        if state["visible"] is None:
            return asked
        return {i for i in asked if i in state["visible"]}

    async def _map(issue_ids):
        return {
            str(i): state["keys"][str(i)] for i in issue_ids if str(i) in state["keys"]
        }

    monkeypatch.setattr(mod, "visible_chain", _chain)
    monkeypatch.setattr(mod, "get_output_citations_repository", lambda: _Repo())
    monkeypatch.setattr(mod, "visible_issue_ids", _visible)
    monkeypatch.setattr(mod, "issue_repository", SimpleNamespace(map_identifiers=_map))
    monkeypatch.setattr(mod, "load_step_shares", AsyncMock(return_value={}))
    return state


async def test_a_version_reports_its_citation_count_in_full(lineage_wired):
    # 被引 3 次、你只看得见 1 条，是允许的诚实答案（spec §2.2）。
    lineage_wired["counts"] = {3: 3}
    lineage_wired["cited"] = {
        3: [
            {
                "issue_id": CITING_ISSUE,
                "message_id": "5001",
                "user_id": ME,
                "at": _AT,
            },
            {
                "issue_id": FOREIGN_CITING_ISSUE,
                "message_id": "5002",
                "user_id": SOMEONE_ELSE,
                "at": _AT,
            },
        ]
    }
    lineage_wired["visible"] = {CITING_ISSUE}
    res = await mod.get_output_lineage("script_shot", "9", _Auth())
    v3 = next(v for v in res.versions if v.version == 3)
    assert v3.cited_count == 3
    assert [c.issue_key for c in v3.cited_in] == ["MH-96"]


async def test_an_uncited_version_says_zero_not_null(lineage_wired):
    res = await mod.get_output_lineage("script_shot", "9", _Auth())
    assert all(v.cited_count == 0 and v.cited_in == [] for v in res.versions)
    # 没被引用过的版本一次反查都不发。
    assert lineage_wired["list_calls"] == []


async def test_the_chain_costs_one_count_query_not_one_per_version(lineage_wired):
    await mod.get_output_lineage("script_shot", "9", _Auth())
    assert lineage_wired["count_calls"] == 1
