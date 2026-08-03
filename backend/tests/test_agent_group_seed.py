"""Seed-side coverage for ``ai_agents.agent_group`` (mig 400, spec 2026-08-02 §B1).

Three things must hold or the grouping silently rots:

1. Every seeded preset carries a group, derived from its slug.
2. ``agent_group`` participates in the seed hash — otherwise re-grouping an
   agent and restarting hits the hash-skip branch and never lands.
3. ``AGENT_GROUP_BY_SLUG`` keys match the ``backend/seeds/agents/`` directory
   names exactly. The map is hand-written; a typo (``topic_scorer`` vs
   ``topic-scorer``) would silently demote an agent to the fallback group.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.repositories.agent_repository import AgentRepository
from app.repositories.skill_repository import SkillRepository
from app.services.ai.runner.seed_loader import (
    AGENT_GROUP_BY_SLUG,
    DEFAULT_AGENT_GROUP,
    SeedLoader,
)

SEEDS_AGENTS_DIR = Path(__file__).resolve().parents[1] / "seeds" / "agents"


def _make_agent_repo(get_by_slug_result: Any = None) -> AsyncMock:
    repo = AsyncMock(spec=AgentRepository)
    repo.get_by_slug.return_value = get_by_slug_result
    repo.update_fields.return_value = {}
    repo.update_skill_bindings.return_value = None
    repo.insert.return_value = {"id": str(uuid4())}
    return repo


def _make_skill_repo() -> AsyncMock:
    repo = AsyncMock(spec=SkillRepository)
    repo.get_by_slug.return_value = None
    repo.list_files.return_value = []
    repo.insert.return_value = {"id": 1}
    return repo


def _loader(tmp_path: Path, agent_repo: AsyncMock) -> SeedLoader:
    return SeedLoader(agent_repo, _make_skill_repo(), tmp_path)


# ─── 1. group derived from slug ───────────────────────────────────────


def test_read_agent_fields_assigns_mapped_group(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents" / "script_ai"
    agent_dir.mkdir(parents=True)
    (agent_dir / "IDENTITY.md").write_text("I am the Script AI — a writer.")

    fields = _loader(tmp_path, _make_agent_repo())._read_agent_fields(
        agent_dir, "script_ai"
    )
    assert fields["agent_group"] == "writing"


def test_read_agent_fields_falls_back_for_unknown_slug(tmp_path: Path) -> None:
    """A slug absent from the map lands in the fallback group, never None."""
    agent_dir = tmp_path / "agents" / "brand-new-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "IDENTITY.md").write_text("I am new — untriaged.")

    fields = _loader(tmp_path, _make_agent_repo())._read_agent_fields(
        agent_dir, "brand-new-agent"
    )
    assert fields["agent_group"] == DEFAULT_AGENT_GROUP


@pytest.mark.asyncio
async def test_insert_carries_agent_group(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agents" / "character-portrait"
    agent_dir.mkdir(parents=True)
    (agent_dir / "IDENTITY.md").write_text("I am the Portrait AI — an artist.")

    agent_repo = _make_agent_repo(get_by_slug_result=None)
    await _loader(tmp_path, agent_repo)._load_agents([])

    row = agent_repo.insert.await_args.args[0]
    assert row["agent_group"] == "art"


# ─── 2. agent_group participates in the seed hash ─────────────────────


@pytest.mark.asyncio
async def test_regrouping_is_not_hash_skipped(tmp_path: Path) -> None:
    """Same content, different group → hash differs → update, not skip."""
    agent_repo = _make_agent_repo()
    loader = _loader(tmp_path, agent_repo)

    base = {
        "slug": "x",
        "name": "X",
        "description": None,
        "identity_md": "same",
        "soul_md": None,
        "agent_md": None,
        "is_system_preset": True,
    }

    agent_repo.get_by_slug.return_value = None
    await loader._upsert_agent("x", {**base, "agent_group": "writing"})
    hash_writing = agent_repo.insert.await_args.args[0]["seed_hash"]

    agent_repo.insert.reset_mock()
    await loader._upsert_agent("x", {**base, "agent_group": "tools"})
    hash_tools = agent_repo.insert.await_args.args[0]["seed_hash"]

    assert hash_writing != hash_tools

    # And the DB-side skip branch must actually fire on the *matching* hash
    # only — regrouping an existing row must take the update path.
    agent_repo.get_by_slug.return_value = {
        "id": str(uuid4()),
        "seed_hash": hash_writing,
    }
    await loader._upsert_agent("x", {**base, "agent_group": "tools"})
    agent_repo.update_fields.assert_awaited_once()
    assert agent_repo.update_fields.await_args.args[1]["agent_group"] == "tools"


# ─── 3. map keys match the seeds directory ────────────────────────────


def test_group_map_keys_match_seed_directories() -> None:
    """Guards against slug typos (underscore vs hyphen) in the hand-written map."""
    on_disk = {p.name for p in SEEDS_AGENTS_DIR.iterdir() if p.is_dir()}
    mapped = set(AGENT_GROUP_BY_SLUG)
    assert mapped - on_disk == set(), "map has slugs with no seed directory"
    assert on_disk - mapped == set(), "seed directory missing from the group map"


def test_group_map_values_are_known_groups() -> None:
    assert set(AGENT_GROUP_BY_SLUG.values()) <= {"writing", "art", "tools"}
    assert DEFAULT_AGENT_GROUP in {"writing", "art", "tools"}
