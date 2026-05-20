"""Unit tests for SeedLoader (mock-based, no real DB)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.ai.runner.seed_loader import SeedLoader

# ─── Fake repo helpers ────────────────────────────────────────────────
#
# Both repos expose `_get_client()` returning an async client; the loader
# calls that directly for inserts. We fake both layers here.


class _FakeInsertQuery:
    """Captures insert() payloads and returns a response object."""

    def __init__(self, returned_id: Any = 1) -> None:
        self.inserted_rows: list[dict[str, Any]] = []
        self._returned_id = returned_id

    def insert(self, row: dict[str, Any]) -> "_FakeInsertQuery":
        self.inserted_rows.append(row)
        return self

    async def execute(self) -> Any:
        class _R:
            data = [{"id": self._returned_id}]

        return _R()


class _FakeClient:
    def __init__(self, insert_query: _FakeInsertQuery) -> None:
        self._q = insert_query
        self.tables: list[str] = []

    def table(self, name: str) -> _FakeInsertQuery:
        self.tables.append(name)
        return self._q


def _make_agent_repo(get_by_slug_result: Any = None) -> AsyncMock:
    """Mock AgentRepository with required async methods."""
    repo = AsyncMock()
    repo.get_by_slug = AsyncMock(return_value=get_by_slug_result)
    repo.update_fields = AsyncMock(return_value={})
    repo.update_skill_bindings = AsyncMock(return_value=None)
    return repo


def _make_skill_repo(
    get_by_slug_result: Any = None,
    list_files_result: Any = None,
) -> AsyncMock:
    """Mock SkillRepository with required async methods."""
    repo = AsyncMock()
    repo.get_by_slug = AsyncMock(return_value=get_by_slug_result)
    repo.update_fields = AsyncMock(return_value={})
    repo.upsert_file = AsyncMock(return_value={})
    repo.list_files = AsyncMock(return_value=list_files_result or [])
    repo.delete_file = AsyncMock(return_value=None)
    return repo


def _attach_fake_insert_client(repo: AsyncMock, insert_id: int) -> _FakeInsertQuery:
    """Wire a fake _get_client() returning a client that captures inserts."""
    insert_q = _FakeInsertQuery(returned_id=insert_id)
    fake_client = _FakeClient(insert_q)

    async def _get_client():
        return fake_client

    repo._get_client = _get_client  # type: ignore[method-assign]
    return insert_q


# ─── _load_agents ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_agents_reads_three_md_files(tmp_path: Path) -> None:
    """All three md files present → insert captures all three fields."""
    agent_dir = tmp_path / "agents" / "test-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "IDENTITY.md").write_text("# Identity\nAn identity.")
    (agent_dir / "SOUL.md").write_text("# Soul\nA soul.")
    (agent_dir / "AGENT.md").write_text("# Agent\nAgent body.")

    agent_repo = _make_agent_repo(get_by_slug_result=None)  # new insert path
    skill_repo = _make_skill_repo()
    insert_q = _attach_fake_insert_client(agent_repo, insert_id=str(uuid4()))

    loader = SeedLoader(agent_repo, skill_repo, tmp_path)
    count = await loader._load_agents([])

    assert count == 1
    assert len(insert_q.inserted_rows) == 1
    row = insert_q.inserted_rows[0]
    assert row["slug"] == "test-agent"
    assert row["name"] == "Test-Agent"  # title-case applied
    assert "An identity." in row["identity_md"]
    assert "A soul." in row["soul_md"]
    assert "Agent body." in row["agent_md"]
    assert row["is_system_preset"] is True


@pytest.mark.asyncio
async def test_load_agents_handles_missing_md_gracefully(tmp_path: Path) -> None:
    """Only IDENTITY.md present → other fields None."""
    agent_dir = tmp_path / "agents" / "skeleton_ai"
    agent_dir.mkdir(parents=True)
    (agent_dir / "IDENTITY.md").write_text("only identity")

    agent_repo = _make_agent_repo(get_by_slug_result=None)
    skill_repo = _make_skill_repo()
    insert_q = _attach_fake_insert_client(agent_repo, insert_id=str(uuid4()))

    loader = SeedLoader(agent_repo, skill_repo, tmp_path)
    await loader._load_agents([])

    row = insert_q.inserted_rows[0]
    assert row["slug"] == "skeleton_ai"
    assert row["name"] == "Skeleton Ai"  # underscore → space, title
    assert row["identity_md"] == "only identity"
    assert row["soul_md"] is None
    assert row["agent_md"] is None


# ─── _load_skills ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_skills_parses_frontmatter(tmp_path: Path) -> None:
    """SKILL.md with frontmatter + body → fields + body captured."""
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: My Skill\n"
        "description: A test skill\n"
        "category: writing\n"
        "icon: 🎯\n"
        "is_public: true\n"
        "---\n"
        "Body of the skill goes here.\n"
    )

    agent_repo = _make_agent_repo()
    skill_repo = _make_skill_repo(get_by_slug_result=None)  # insert path
    insert_q = _attach_fake_insert_client(skill_repo, insert_id=42)

    loader = SeedLoader(agent_repo, skill_repo, tmp_path)
    count = await loader._load_skills([])

    assert count == 1
    assert len(insert_q.inserted_rows) == 1
    row = insert_q.inserted_rows[0]
    assert row["slug"] == "my-skill"
    assert row["name"] == "My Skill"
    assert row["description"] == "A test skill"
    assert row["category"] == "writing"
    assert row["icon"] == "🎯"
    assert row["is_public"] is True
    assert row["status"] == "active"
    assert "Body of the skill" in row["body_md"]
    # frontmatter_json preserved whole
    assert row["frontmatter_json"]["name"] == "My Skill"
    assert row["frontmatter_json"]["category"] == "writing"


@pytest.mark.asyncio
async def test_load_skills_loads_reference_file(tmp_path: Path) -> None:
    """SKILL.md + references/example.md → 1 skill + 1 file upsert."""
    skill_dir = tmp_path / "skills" / "my-skill"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: My Skill\n---\nbody\n")
    (skill_dir / "references" / "example.md").write_text("# Example ref")

    agent_repo = _make_agent_repo()
    skill_repo = _make_skill_repo(get_by_slug_result=None)
    _attach_fake_insert_client(skill_repo, insert_id=99)

    loader = SeedLoader(agent_repo, skill_repo, tmp_path)
    await loader._load_skills([])

    skill_repo.upsert_file.assert_awaited_once()
    kwargs = skill_repo.upsert_file.await_args.kwargs
    args = skill_repo.upsert_file.await_args.args
    # first positional arg is skill_id
    assert args[0] == 99
    assert kwargs["path"] == "references/example.md"
    assert kwargs["file_type"] == "markdown"
    assert "# Example ref" in kwargs["content"]


@pytest.mark.asyncio
async def test_load_skills_categorizes_scripts(tmp_path: Path) -> None:
    """scripts/validate.py → file_type='script'."""
    skill_dir = tmp_path / "skills" / "my-skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: My Skill\n---\nbody\n")
    (skill_dir / "scripts" / "validate.py").write_text("print('validate')\n")

    agent_repo = _make_agent_repo()
    skill_repo = _make_skill_repo(get_by_slug_result=None)
    _attach_fake_insert_client(skill_repo, insert_id=7)

    loader = SeedLoader(agent_repo, skill_repo, tmp_path)
    await loader._load_skills([])

    skill_repo.upsert_file.assert_awaited_once()
    kwargs = skill_repo.upsert_file.await_args.kwargs
    assert kwargs["path"] == "scripts/validate.py"
    assert kwargs["file_type"] == "script"


# ─── _bind_script_ai_skills ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_bindings_set_when_all_three_skills_present(
    tmp_path: Path,
) -> None:
    """All 3 target skills exist + script_ai agent exists → bindings=3."""
    agent_id = str(uuid4())
    agent_repo = _make_agent_repo(
        get_by_slug_result={"id": agent_id, "slug": "script_ai"}
    )

    # Return a resolved skill for each slug
    skill_rows = {
        "script-outline": {"id": 10},
        "script-expand": {"id": 20},
        "script-branch": {"id": 30},
    }

    async def _get_skill_by_slug(slug: str) -> Any:
        return skill_rows.get(slug)

    skill_repo = _make_skill_repo()
    skill_repo.get_by_slug = AsyncMock(side_effect=_get_skill_by_slug)

    loader = SeedLoader(agent_repo, skill_repo, tmp_path)
    bound = await loader._bind_script_ai_skills([])

    assert bound == 3
    agent_repo.update_skill_bindings.assert_awaited_once()
    args = agent_repo.update_skill_bindings.await_args.args
    assert args[1] == [10, 20, 30]


@pytest.mark.asyncio
async def test_bindings_skip_when_script_ai_missing(
    tmp_path: Path,
) -> None:
    """script_ai agent not found → bindings returns 0, never calls update."""
    agent_repo = _make_agent_repo(get_by_slug_result=None)
    skill_repo = _make_skill_repo()

    loader = SeedLoader(agent_repo, skill_repo, tmp_path)
    bound = await loader._bind_script_ai_skills([])

    assert bound == 0
    agent_repo.update_skill_bindings.assert_not_awaited()


# ─── _load_skill_subfiles reconcile (V6pre②) ─────────────────────────


@pytest.mark.asyncio
async def test_load_skill_subfiles_deletes_orphans(tmp_path: Path) -> None:
    """Disk has references/a.md; DB has a.md + stale old.md.
    After reconcile, DB upsert on a.md and delete on old.md."""
    skill_dir = tmp_path / "skills" / "demo"
    refs = skill_dir / "references"
    refs.mkdir(parents=True)
    (refs / "a.md").write_text("content A")

    skill_repo = _make_skill_repo(
        list_files_result=[
            {"path": "references/a.md", "file_type": "markdown"},
            {"path": "references/old.md", "file_type": "markdown"},
            {"path": "scripts/run.sh", "file_type": "script"},  # disk has none
        ]
    )
    agent_repo = _make_agent_repo()

    loader = SeedLoader(agent_repo, skill_repo, tmp_path)
    await loader._load_skill_subfiles(skill_id=99, skill_dir=skill_dir)

    # a.md upserted
    upsert_calls = skill_repo.upsert_file.await_args_list
    upserted_paths = {c.kwargs.get("path") for c in upsert_calls}
    assert "references/a.md" in upserted_paths

    # Both orphans deleted (references/old.md and scripts/run.sh)
    delete_calls = skill_repo.delete_file.await_args_list
    deleted_paths = {c.args[1] for c in delete_calls}
    assert deleted_paths == {"references/old.md", "scripts/run.sh"}


@pytest.mark.asyncio
async def test_load_skill_subfiles_preserves_user_files(tmp_path: Path) -> None:
    """User-authored files outside managed prefixes (SKILL.md variants,
    custom/ subtree) must NOT be deleted by the reconcile pass."""
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    # No disk sub-files at all

    skill_repo = _make_skill_repo(
        list_files_result=[
            # Hypothetical user-created path, outside references/scripts/assets
            {"path": "custom/extra.md", "file_type": "markdown"},
        ]
    )
    agent_repo = _make_agent_repo()

    loader = SeedLoader(agent_repo, skill_repo, tmp_path)
    await loader._load_skill_subfiles(skill_id=77, skill_dir=skill_dir)

    # custom/ is outside the managed subtree — reconcile must leave it alone
    skill_repo.delete_file.assert_not_awaited()


# ─── _extract_description_from_identity ────────────────────────────────


def test_extract_description_from_identity_em_dash() -> None:
    """Standard ``I am the MediaHub X AI — <description>.`` shape."""
    from app.services.ai.runner.seed_loader import _extract_description_from_identity

    body = (
        "I am the MediaHub Visual Analyze AI — a multimodal vision analyst.\n"
        "Second line shouldn't leak into the description."
    )
    assert _extract_description_from_identity(body) == "a multimodal vision analyst"


def test_extract_description_from_identity_strips_trailing_period() -> None:
    from app.services.ai.runner.seed_loader import _extract_description_from_identity

    assert (
        _extract_description_from_identity("foo — a compact summarizer.")
        == "a compact summarizer"
    )


def test_extract_description_from_identity_falls_back_to_ascii_hyphen() -> None:
    from app.services.ai.runner.seed_loader import _extract_description_from_identity

    assert (
        _extract_description_from_identity("foo - plain ascii dash variant")
        == "plain ascii dash variant"
    )


def test_extract_description_from_identity_returns_none_when_unparseable() -> None:
    """No separator → None (we'd rather show blank than dump the whole opener)."""
    from app.services.ai.runner.seed_loader import _extract_description_from_identity

    assert _extract_description_from_identity(None) is None
    assert _extract_description_from_identity("") is None
    assert (
        _extract_description_from_identity("just a sentence with no separator") is None
    )


# ─── _format_error ────────────────────────────────────────────────────


def test_format_error_plain_exception() -> None:
    """Plain Exception → message only, no code/status."""
    from app.services.ai.runner.seed_loader import _format_error

    err = _format_error(ValueError("boom"))
    assert err["type"] == "ValueError"
    assert err["message"] == "boom"
    assert err.get("code") is None
    assert err.get("status") is None


def test_format_error_postgrest_apierror_shape() -> None:
    """Postgrest-style error with code/message/details/hint → all extracted."""
    from app.services.ai.runner.seed_loader import _format_error

    class _FakePostgrestError(Exception):
        code = "42501"
        message = "new row violates row-level security policy"
        details = "for table ai_agents"
        hint = None

        def __str__(self) -> str:
            return self.message

    err = _format_error(_FakePostgrestError())
    assert err["type"] == "_FakePostgrestError"
    assert err["message"] == "new row violates row-level security policy"
    assert err["code"] == "42501"
    assert err["details"] == "for table ai_agents"


def test_format_error_httpx_status() -> None:
    """Exception with `response.status_code` attr → status extracted."""
    from app.services.ai.runner.seed_loader import _format_error

    class _FakeResp:
        status_code = 503

    class _FakeHttpErr(Exception):
        response = _FakeResp()

    err = _format_error(_FakeHttpErr("service unavailable"))
    assert err["status"] == 503


# ─── load_all error aggregation ───────────────────────────────────────


@pytest.mark.asyncio
async def test_load_all_aggregates_per_agent_errors(tmp_path: Path) -> None:
    """Agent A inserts OK, agent B fails → load_all returns errors list
    with agent_b entry AND still reports agents=1 counted successfully."""
    (tmp_path / "agents" / "agent_a").mkdir(parents=True)
    (tmp_path / "agents" / "agent_a" / "IDENTITY.md").write_text("a")
    (tmp_path / "agents" / "agent_b").mkdir(parents=True)
    (tmp_path / "agents" / "agent_b" / "IDENTITY.md").write_text("b")

    agent_repo = _make_agent_repo(get_by_slug_result=None)

    # First insert succeeds, second raises
    call_count = {"n": 0}

    async def _flaky_get_client():
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated RLS denial")

        class _OK:
            def table(self, _):
                class _Q:
                    def insert(self, _row):
                        return self

                    async def execute(self):
                        class _R:
                            data = [{"id": str(uuid4())}]

                        return _R()

                return _Q()

        return _OK()

    agent_repo._get_client = _flaky_get_client  # type: ignore[method-assign]
    skill_repo = _make_skill_repo()

    loader = SeedLoader(agent_repo, skill_repo, tmp_path)
    results = await loader.load_all()

    assert results["agents"] == 1
    assert "errors" in results
    agent_errors = [e for e in results["errors"] if e["scope"] == "agent"]
    assert len(agent_errors) == 1
    assert agent_errors[0]["slug"] == "agent_b"
    assert agent_errors[0]["error"]["type"] == "RuntimeError"
    assert "RLS" in agent_errors[0]["error"]["message"]
