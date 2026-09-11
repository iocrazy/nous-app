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


def _client(monkeypatch, rows=SEEDED, *, visible=True, owner=ME, diff=None):
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
        mod, "build_diff", AsyncMock(return_value=diff if diff is not None else {})
    )
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
    assert top["cost_cents"] == 1.25
    assert top["turn"] == 2 and top["step"] == 3


def test_lineage_carries_the_issue_key_and_a_deep_link(monkeypatch):
    """3a Task 3b: ``issue_id`` alone is unusable — the issue route is keyed by
    the identifier inside a team, so the panel could only ever draw a disabled
    button. The endpoint now says WHICH issue and WHERE it lives."""
    top = (
        _client(monkeypatch).get("/api/v1/outputs/script_shot/9").json()["versions"][0]
    )
    assert top["issue_key"] == ISSUE_KEY
    assert top["deep_link"] == f"/team/{TEAM_ID}/todolist/{ISSUE_KEY}?step=3"


def test_the_step_anchor_is_part_of_the_link(monkeypatch):
    """A run of thirty steps opens on the step that produced THIS version, not
    at the top of the issue."""
    rows = [_row(1, step=7)]
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
    rows = [_row(1, issue_id=None)]
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


# ── diff ─────────────────────────────────────────────────────────────────


def test_diff_rejects_a_version_that_does_not_exist(monkeypatch):
    r = _client(monkeypatch).get("/api/v1/outputs/script_shot/9/diff?from=1&to=7")
    assert r.status_code == 404
    assert r.json()["details"]["code"] == "version_not_found"


def test_diff_of_an_unregistered_object_is_not_registered(monkeypatch):
    r = _client(monkeypatch).get("/api/v1/outputs/script_shot/404/diff?from=1&to=2")
    assert r.status_code == 404
    assert r.json()["details"]["code"] == "not_registered"


def test_diff_returns_two_sides_keyed_from_and_to(monkeypatch):
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
    diff = {
        "kind": "script_shot",
        "ref_id": "9",
        "content_type": "text",
        "from": side,
        "to": {**side, "version": 2, "text": "description: a close-up"},
    }
    r = _client(monkeypatch, diff=diff).get(
        "/api/v1/outputs/script_shot/9/diff?from=1&to=2"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content_type"] == "text"
    assert body["from"]["version"] == 1 and body["to"]["version"] == 2
    assert body["from"]["text"] == "description: a wide shot"
    assert body["to"]["issue_id"] == ISSUE_ID


def test_diff_checks_visibility_before_reading_content(monkeypatch):
    c = _client(monkeypatch, visible=False)
    assert c.get("/api/v1/outputs/script_shot/9/diff?from=1&to=2").status_code == 404
    mod.build_diff.assert_not_awaited()
