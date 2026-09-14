"""``GET /api/v1/issues/{id}/outputs`` — everything this issue produced.

Grouped by ``(kind, ref_id)``: the panel's unit is the OBJECT, not the row.
Three revisions of one shot are one entry with three versions, never three
entries — otherwise "what did this issue produce" reads as more work than
happened.

Visibility is the issue's own (``_assert_visibility``), and a missing issue is
404 before anything is read — the registry must not answer for an issue the
caller cannot see.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException

mod = importlib.import_module("app.api.issues_router")

pytestmark = pytest.mark.unit

ME = "11111111-1111-1111-1111-111111111111"
RUN_A = "913402881190401"
RUN_B = "913402881190402"
ISSUE_ID = 348087075560200
ISSUE_KEY = "MH-91"
#: ``issues.team_id`` stays a NATIVE int out of the issue repository (the
#: BIGINT parity rule) — the link builder has to cope with that, not with a
#: string the test invented.
TEAM_ID = 424242424242


class _Auth:
    user_id = ME


def _row(kind, ref_id, version, run_id=RUN_A, **over):
    row = {
        "id": str(700000000000000 + version),
        "run_id": run_id,
        "seq": version,
        "kind": kind,
        "ref_id": ref_id,
        "version": version,
        "parent_version": version - 1 if version > 1 else None,
        "title": f"{kind} {ref_id} v{version}",
        "model": "qwen-max",
        "cost_cents": 0.5,
        "turn": 1,
        "step": version,
        "created_at": f"2026-09-1{version}T00:00:00+00:00",
    }
    row.update(over)
    return row


#: The repository's own order: kind, ref_id, version DESC.
ROWS = [
    _row("generated_media", "500", 1, run_id=RUN_B),
    _row("script_shot", "9", 2),
    _row("script_shot", "9", 1),
    _row("script_shot", "10", 1),
]


class _Repo:
    def __init__(self, rows=ROWS):
        self.rows = rows
        self.seen: list = []

    async def list_for_issue(self, issue_id):
        self.seen.append(issue_id)
        return list(self.rows)


def _patch(
    monkeypatch,
    *,
    visible=True,
    issue=True,
    repo=None,
    identifier=ISSUE_KEY,
    team_id=TEAM_ID,
):
    repo = repo or _Repo()

    class _Issues:
        async def get_by_id(self, issue_id):
            if not issue:
                return None
            # The SELECT *-shaped row the repository really returns.
            return {"id": issue_id, "identifier": identifier, "team_id": team_id}

    async def _visible(row, user_id):
        return visible

    monkeypatch.setattr(mod, "issue_repository", _Issues())
    monkeypatch.setattr(mod, "is_issue_visible", _visible)
    monkeypatch.setattr(mod, "get_run_deliverables_repository", lambda: repo)
    return repo


@pytest.mark.asyncio
async def test_outputs_group_by_object_newest_version_first(monkeypatch):
    _patch(monkeypatch)
    body = (await mod.list_issue_outputs(348087075560200, _Auth())).model_dump()

    assert [(i["kind"], i["ref_id"]) for i in body["items"]] == [
        ("generated_media", "500"),
        ("script_shot", "9"),
        ("script_shot", "10"),
    ]
    shot9 = body["items"][1]
    assert shot9["latest_version"] == 2
    assert [v["version"] for v in shot9["versions"]] == [2, 1]
    # The object's title is the LATEST version's — an old title on a revised
    # object is the wrong name for the thing that exists now.
    assert shot9["title"] == "script_shot 9 v2"


@pytest.mark.asyncio
async def test_ids_stay_strings(monkeypatch):
    _patch(monkeypatch)
    body = (await mod.list_issue_outputs(348087075560200, _Auth())).model_dump()
    version = body["items"][0]["versions"][0]
    assert isinstance(version["run_id"], str) and version["run_id"] == RUN_B
    assert isinstance(version["id"], str)


@pytest.mark.asyncio
async def test_an_issue_with_no_outputs_is_an_empty_list_not_a_404(monkeypatch):
    """Unlike the per-object endpoint: the ISSUE exists and is visible, it just
    produced nothing yet. That is a real, renderable answer."""
    _patch(monkeypatch, repo=_Repo(rows=[]))
    body = (await mod.list_issue_outputs(348087075560200, _Auth())).model_dump()
    assert body == {"items": []}


@pytest.mark.asyncio
async def test_an_invisible_issue_is_404_and_reads_nothing(monkeypatch):
    repo = _patch(monkeypatch, visible=False)
    with pytest.raises(HTTPException) as exc:
        await mod.list_issue_outputs(348087075560200, _Auth())
    assert exc.value.status_code == 404
    assert repo.seen == []


@pytest.mark.asyncio
async def test_a_missing_issue_is_404(monkeypatch):
    repo = _patch(monkeypatch, issue=False)
    with pytest.raises(HTTPException) as exc:
        await mod.list_issue_outputs(348087075560200, _Auth())
    assert exc.value.status_code == 404
    assert repo.seen == []


# ── issue key + deep link (3a Task 3b) ───────────────────────────────────


@pytest.mark.asyncio
async def test_every_version_carries_its_issue_key_and_deep_link(monkeypatch):
    """The panel's ``Open Issue`` needs ``/team/{team}/todolist/{key}``; the
    issue_id it used to get cannot build that. The issue is already loaded for
    the visibility check, so this costs no extra query — no per-row re-JOIN."""
    _patch(monkeypatch)
    body = (await mod.list_issue_outputs(ISSUE_ID, _Auth())).model_dump()

    shot9 = body["items"][1]
    assert [v["issue_key"] for v in shot9["versions"]] == [ISSUE_KEY, ISSUE_KEY]
    # step is the version number in this fixture — each version opens on the
    # step that produced it.
    assert [v["deep_link"] for v in shot9["versions"]] == [
        f"/team/{TEAM_ID}/todolist/{ISSUE_KEY}?step=2&turn=1",
        f"/team/{TEAM_ID}/todolist/{ISSUE_KEY}?step=1&turn=1",
    ]


@pytest.mark.asyncio
async def test_an_issue_without_an_identifier_gets_no_key_and_no_link(monkeypatch):
    """Never a URL built from the snowflake: ``/todolist/348087075560200``
    resolves to nothing."""
    _patch(monkeypatch, identifier=None)
    body = (await mod.list_issue_outputs(ISSUE_ID, _Auth())).model_dump()
    top = body["items"][0]["versions"][0]
    assert top["issue_key"] is None and top["deep_link"] is None
    assert top["issue_id"] == str(ISSUE_ID)


@pytest.mark.asyncio
async def test_a_personal_issue_keeps_its_key_but_has_no_link(monkeypatch):
    """``team_id IS NULL`` on a personal-scope issue. The key is still worth
    printing; the URL is not buildable."""
    _patch(monkeypatch, team_id=None)
    top = (await mod.list_issue_outputs(ISSUE_ID, _Auth())).model_dump()["items"][0][
        "versions"
    ][0]
    assert top["issue_key"] == ISSUE_KEY and top["deep_link"] is None


@pytest.mark.asyncio
async def test_a_human_row_keeps_the_issue_key_but_gets_no_deep_link(monkeypatch):
    """3b 回退：``run_id IS NULL`` 的人手版。

    ``issue_key`` 要留着——这一版确实长在这件工作的链上，读者要知道是哪件。
    ``deep_link`` 必须是 None：链接的锚点是 ``(turn, step)``，也就是某次运行的
    某一步；人手版没有那一步，给它一条 URL 等于声称这一版是那一步产生的。
    ``turn`` / ``step`` 同理清空——轨迹坐标是 run 的东西。
    """
    rows = [
        _row(
            "script_shot",
            "9",
            3,
            run_id=None,
            actor_user_id=ME,
            reverted_from_version=1,
            seq=None,
            turn=None,
            step=None,
        ),
        _row("script_shot", "9", 2),
    ]
    _patch(monkeypatch, repo=_Repo(rows))
    body = (await mod.list_issue_outputs(ISSUE_ID, _Auth())).model_dump()

    human, agent = body["items"][0]["versions"]
    assert human["version"] == 3 and human["run_id"] is None
    assert human["actor_user_id"] == ME
    assert human["reverted_from_version"] == 1
    assert human["issue_id"] == str(ISSUE_ID)
    assert human["issue_key"] == ISSUE_KEY
    assert human["deep_link"] is None
    assert human["turn"] is None and human["step"] is None
    # 正向对照：同一批里有 run 的那一版照常拿到链接。
    assert agent["deep_link"] == f"/team/{TEAM_ID}/todolist/{ISSUE_KEY}?step=2&turn=1"


@pytest.mark.asyncio
async def test_a_human_row_with_stray_coordinates_still_gets_no_link(monkeypatch):
    """就算行上带着 turn / step（不该有，但别靠「不该有」兜底），投影也要清掉
    它们——否则 ``issue_deep_link`` 会照样拼出一条锚到别人那一步的 URL。"""
    rows = [_row("script_shot", "9", 3, run_id=None, actor_user_id=ME)]
    _patch(monkeypatch, repo=_Repo(rows))
    body = (await mod.list_issue_outputs(ISSUE_ID, _Auth())).model_dump()
    human = body["items"][0]["versions"][0]
    assert human["turn"] is None and human["step"] is None
    assert human["deep_link"] is None
