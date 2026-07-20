"""Create-project → topic auto-produced linkage (M1.5, best-effort).

A project born from a topic (topic_id in the create payload) marks that topic
produced, scoped to the topic's own team. The linkage is enrichment: a failure
in the topics repo must never fail project creation.
"""

from __future__ import annotations

import pytest

import app.repositories.topics_repository as topics_repo_mod
from app.services.library.projects_service import ProjectsService


class _FakeStages:
    async def list_catalog(self):
        return []  # skip default-stage init to keep the test focused


class _FakeRepo:
    def __init__(self):
        self.received = None

    async def create_project(self, data):
        self.received = data
        # topic_id rides through as a real projects column
        return {"id": 9001, "name": data.get("name"), "topic_id": data.get("topic_id")}


class _FakeTopicsRepo:
    def __init__(self, *, team_id="777", boom=False):
        self._team_id = team_id
        self._boom = boom
        self.produced = []

    async def get_topic_team_id(self, topic_id):
        return self._team_id

    async def mark_produced(self, topic_id, team_id):
        if self._boom:
            raise RuntimeError("topics table down")
        self.produced.append((str(topic_id), str(team_id)))
        return True


def _svc():
    svc = ProjectsService.__new__(ProjectsService)
    svc.repo = _FakeRepo()
    svc._stages_repo_override = _FakeStages()
    return svc


@pytest.mark.asyncio
async def test_create_from_topic_marks_produced(monkeypatch):
    topics = _FakeTopicsRepo(team_id="777")
    monkeypatch.setattr(topics_repo_mod, "get_topics_repository", lambda: topics)
    svc = _svc()

    out = await svc.create_project("user-1", {"name": "From Topic", "topic_id": "555"})

    assert out["id"] == 9001
    # topic_id persisted on the project row (stayed in the insert payload)
    assert svc.repo.received["topic_id"] == "555"
    # marked produced, scoped to the topic's own team
    assert topics.produced == [("555", "777")]


@pytest.mark.asyncio
async def test_create_without_topic_does_not_touch_topics(monkeypatch):
    topics = _FakeTopicsRepo()
    monkeypatch.setattr(topics_repo_mod, "get_topics_repository", lambda: topics)
    svc = _svc()

    await svc.create_project("user-1", {"name": "Plain Project"})

    assert topics.produced == []


@pytest.mark.asyncio
async def test_mark_produced_failure_does_not_block_create(monkeypatch):
    topics = _FakeTopicsRepo(boom=True)
    monkeypatch.setattr(topics_repo_mod, "get_topics_repository", lambda: topics)
    svc = _svc()

    out = await svc.create_project("user-1", {"name": "Resilient", "topic_id": "555"})

    # creation still succeeds despite the topics repo blowing up
    assert out["id"] == 9001
