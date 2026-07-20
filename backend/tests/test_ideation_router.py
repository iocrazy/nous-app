"""Ideation topic-pool router (M1.5).

Handlers called directly (as in test_workflows_router) with the repo and
``resolve_effective_role`` monkeypatched. Covers each endpoint plus the 403/404
authz branches (404 before 403, no existence leak) and the schema validators.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.ideation import TopicCreate, TopicUpdate

router_mod = importlib.import_module("app.api.ideation_router")


class _Auth:
    user_id = "00000000-0000-0000-0000-000000000001"


_AUTH = _Auth()


class _FakeRepo:
    def __init__(
        self,
        *,
        team_id_for_topic: Optional[str] = "777",
        topic: Optional[Dict[str, Any]] = None,
        delete_ok: bool = True,
    ):
        self._team_id_for_topic = team_id_for_topic
        self._topic = topic
        self._delete_ok = delete_ok
        self.created: List[Dict[str, Any]] = []
        self.updated: List[Dict[str, Any]] = []
        self.deleted: List[str] = []
        self.listed: List[Dict[str, Any]] = []

    async def list_topics(self, team_id, *, status=None):
        self.listed.append({"team_id": team_id, "status": status})
        return [{"id": "1", "title": "Idea", "status": status or "candidate"}]

    async def create_topic(self, team_id, **kwargs):
        row = {"id": "500", "team_id": str(team_id), "status": "candidate", **kwargs}
        self.created.append(row)
        return row

    async def get_topic_team_id(self, topic_id):
        return self._team_id_for_topic

    async def get_topic(self, topic_id, team_id):
        return self._topic

    async def update_topic(self, topic_id, team_id, **kwargs):
        self.updated.append({"id": topic_id, **kwargs})
        return self._topic

    async def delete_topic(self, topic_id, team_id):
        self.deleted.append(str(topic_id))
        return self._delete_ok


def _install(monkeypatch, repo, *, role="manager"):
    monkeypatch.setattr(router_mod, "get_topics_repository", lambda: repo)

    async def _role(user_id, *, project_id=None, team_id=None):
        return role

    monkeypatch.setattr(router_mod, "resolve_effective_role", _role)


# ── list ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_member_ok(monkeypatch):
    repo = _FakeRepo()
    _install(monkeypatch, repo, role="viewer")
    r = await router_mod.list_topics(_AUTH, team_id="777", status=None)
    assert r["success"] is True
    assert r["data"][0]["title"] == "Idea"


@pytest.mark.asyncio
async def test_list_forwards_status_filter(monkeypatch):
    repo = _FakeRepo()
    _install(monkeypatch, repo, role="editor")
    await router_mod.list_topics(_AUTH, team_id="777", status="shortlisted")
    assert repo.listed[0]["status"] == "shortlisted"


@pytest.mark.asyncio
async def test_list_non_member_403(monkeypatch):
    repo = _FakeRepo()
    _install(monkeypatch, repo, role=None)
    with pytest.raises(HTTPException) as exc:
        await router_mod.list_topics(_AUTH, team_id="777", status=None)
    assert exc.value.status_code == 403


# ── create ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_member_ok(monkeypatch):
    repo = _FakeRepo()
    _install(monkeypatch, repo, role="manager")
    r = await router_mod.create_topic(
        TopicCreate(title="Spring Launch"), _AUTH, team_id="777"
    )
    assert r["data"]["id"] == "500"
    assert repo.created


@pytest.mark.asyncio
async def test_create_viewer_insufficient_403(monkeypatch):
    repo = _FakeRepo()
    _install(monkeypatch, repo, role="viewer")
    with pytest.raises(HTTPException) as exc:
        await router_mod.create_topic(TopicCreate(title="X"), _AUTH, team_id="777")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_create_non_member_403(monkeypatch):
    repo = _FakeRepo()
    _install(monkeypatch, repo, role=None)
    with pytest.raises(HTTPException) as exc:
        await router_mod.create_topic(TopicCreate(title="X"), _AUTH, team_id="777")
    assert exc.value.status_code == 403


# ── get ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_member_ok(monkeypatch):
    repo = _FakeRepo(topic={"id": "500", "title": "Idea"})
    _install(monkeypatch, repo, role="viewer")
    r = await router_mod.get_topic("500", _AUTH)
    assert r["data"]["id"] == "500"


@pytest.mark.asyncio
async def test_get_missing_404(monkeypatch):
    repo = _FakeRepo(team_id_for_topic=None)
    _install(monkeypatch, repo, role="manager")
    with pytest.raises(HTTPException) as exc:
        await router_mod.get_topic("500", _AUTH)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_non_member_404_no_leak(monkeypatch):
    repo = _FakeRepo(topic={"id": "500"})
    _install(monkeypatch, repo, role=None)
    with pytest.raises(HTTPException) as exc:
        await router_mod.get_topic("500", _AUTH)
    # 404 (not 403) so existence never leaks across teams.
    assert exc.value.status_code == 404


# ── update (status flow) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_status_flow_ok(monkeypatch):
    repo = _FakeRepo(topic={"id": "500", "status": "shortlisted"})
    _install(monkeypatch, repo, role="editor")
    r = await router_mod.update_topic("500", TopicUpdate(status="shortlisted"), _AUTH)
    assert r["data"]["status"] == "shortlisted"
    # exclude_unset: only status forwarded, others stay None (untouched)
    assert repo.updated[0]["status"] == "shortlisted"
    assert repo.updated[0]["title"] is None


@pytest.mark.asyncio
async def test_update_non_member_404(monkeypatch):
    repo = _FakeRepo(topic={"id": "500"})
    _install(monkeypatch, repo, role=None)
    with pytest.raises(HTTPException) as exc:
        await router_mod.update_topic("500", TopicUpdate(title="X"), _AUTH)
    assert exc.value.status_code == 404


# ── delete ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_member_ok(monkeypatch):
    repo = _FakeRepo(delete_ok=True)
    _install(monkeypatch, repo, role="manager")
    r = await router_mod.delete_topic("500", _AUTH)
    assert r["data"]["deleted"] is True
    assert repo.deleted == ["500"]


@pytest.mark.asyncio
async def test_delete_missing_404(monkeypatch):
    repo = _FakeRepo(team_id_for_topic=None)
    _install(monkeypatch, repo, role="manager")
    with pytest.raises(HTTPException) as exc:
        await router_mod.delete_topic("500", _AUTH)
    assert exc.value.status_code == 404


# ── schema validators ────────────────────────────────────────────────────────


def test_topic_create_rejects_two_sources():
    with pytest.raises(ValidationError):
        TopicCreate(title="X", note_id="1", resource_id="2")


def test_topic_create_allows_single_source():
    t = TopicCreate(title="X", note_id="1")
    assert t.note_id == "1"


def test_topic_create_allows_blank():
    t = TopicCreate(title="X")
    assert all(
        getattr(t, f) is None
        for f in ("note_id", "resource_id", "media_id", "inspiration_topic_id")
    )


def test_topic_update_rejects_bad_status():
    with pytest.raises(ValidationError):
        TopicUpdate(status="done")
