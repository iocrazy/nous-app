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


def _patch(monkeypatch, *, visible=True, issue=True, repo=None):
    repo = repo or _Repo()

    class _Issues:
        async def get_by_id(self, issue_id):
            return {"id": issue_id} if issue else None

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
