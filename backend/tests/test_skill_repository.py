"""Unit tests for SkillRepository (SQLAlchemy 2.0 ORM, skills + skill_files).

Post-rollout the repository IS the SQLAlchemy 2.0 implementation — the legacy
supabase-py REST path was retired with USE_ORM_SKILL. These tests mock
``read_scope``/``write_scope`` with a fake session that captures every emitted
``(compiled sql, binds)`` pair and returns configured ORM row objects, so the
compiled SQL shape + bind params AND the strategy-C value-type parity sweep
(skills.id BIGINT → native int, skill_files.id uuid → str, skills.created_by
uuid → str, created_at timestamptz → ISO str, frontmatter_json jsonb → native
dict) are asserted WITHOUT a live database (the DSN-gated integration suite in
``tests/integration/test_skill_repository_orm.py`` exercises the real
round-trip). This keeps fast, always-run coverage of the collapsed ORM bodies.

Methods with multiple statements (the versioned snapshot-then-update writes)
queue successive results via ``fake_session.queue(...)``; single-statement
methods set ``fake_session.rows`` / ``.tuples`` directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.skill_repository as mod
from app.models import SkillFiles, Skills
from app.repositories.skill_repository import SkillRepository

# ─── ORM fake session ──────────────────────────────────────────────


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    try:
        params = dict(compiled.params)
    except Exception:  # pragma: no cover - text() with unbound params
        params = {}
    return str(compiled), params


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows: list[Any], tuples: list[Any]) -> None:
        self._rows = rows
        self._tuples = tuples

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)

    def all(self) -> list[Any]:
        return list(self._tuples)


class _FakeSession:
    """Captures execute (compiled sql, merged binds); returns configured ORM
    rows / tuples. Successive statements pop the result queue if populated,
    otherwise fall back to the ``rows``/``tuples`` defaults."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[Any] = []  # scalars().all() / scalars().first()
        self.tuples: list[Any] = []  # result.all() (row tuples)
        self._queue: list[tuple[list[Any], list[Any]]] = []

    def queue(self, rows: list[Any] | None = None, tuples: list[Any] | None = None):
        self._queue.append((rows or [], tuples or []))
        return self

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        sql, binds = _compile(stmt)
        if params:
            binds = {**binds, **params}
        self.calls.append((sql, binds))
        if self._queue:
            rows, tuples = self._queue.pop(0)
            return _FakeResult(rows, tuples)
        return _FakeResult(self.rows, self.tuples)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> SkillRepository:
    return SkillRepository()


# ─── get_by_slug ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_slug_returns_none_for_missing(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    assert await repo.get_by_slug("nonexistent_slug") is None

    sql, binds = fake_session.calls[-1]
    assert sql.startswith("SELECT")
    assert "skills" in sql
    assert "nonexistent_slug" in binds.values()


@pytest.mark.asyncio
async def test_get_by_slug_returns_row_with_parity(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    fake_session.rows = [
        Skills(id=42, slug="translate", name="Translate", created_at=created)
    ]
    result = await repo.get_by_slug("translate")
    assert result is not None
    assert result["slug"] == "translate"
    assert type(result["id"]) is int and result["id"] == 42  # BIGINT → native int
    assert result["created_at"] == created.isoformat()  # tstz → ISO str


@pytest.mark.asyncio
async def test_get_by_slug_returns_none_on_exception(
    repo: SkillRepository, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read errors are swallowed and return None."""

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "read_scope", _boom)
    assert await repo.get_by_slug("translate") is None


# ─── get_by_id ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_id_coerces_str_id_to_int_bind(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [Skills(id=10, slug="s", name="S")]
    row = await repo.get_by_id("10")
    assert row is not None and row["id"] == 10

    sql, binds = fake_session.calls[-1]
    assert "skills.id =" in sql
    assert 10 in binds.values()  # str "10" → native int bind


# ─── list_by_ids ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_by_ids_returns_empty_for_empty_input(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    """Short-circuits on empty input — never touches the session."""
    assert await repo.list_by_ids([]) == []
    assert fake_session.calls == []


@pytest.mark.asyncio
async def test_list_by_ids_uses_in_clause(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [Skills(id=1), Skills(id=2), Skills(id=3)]
    result = await repo.list_by_ids([1, 2, 3])
    assert [r["id"] for r in result] == [1, 2, 3]

    sql, binds = fake_session.calls[-1]
    assert "IN" in sql
    assert [1, 2, 3] in binds.values()  # expanding IN bind, coerced to int


# ─── list_skills ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_skills_summary_projection_and_active_filter(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [
        Skills(id=7, name="Pub", is_public=True, status="active", team_id=None)
    ]
    skills = await repo.list_skills()
    assert len(skills) == 1
    # summary projection: content_md excluded, only _SUMMARY_COLS keys present.
    assert set(skills[0].keys()) == set(mod._SUMMARY_COLS)
    assert "content_md" not in skills[0]
    assert type(skills[0]["id"]) is int

    sql, _ = fake_session.calls[-1]
    assert "status =" in sql
    assert "IS NULL" in sql  # project_id IS NULL (no project_id passed)
    assert "ORDER BY" in sql and "created_at DESC" in sql


@pytest.mark.asyncio
async def test_list_skills_team_widens_or_and_coerces_team_id(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.list_skills(team_id="55", project_id="66", category="writing")

    sql, binds = fake_session.calls[-1]
    assert 55 in binds.values()  # team_id "55" → native int
    assert 66 in binds.values()  # project_id "66" → native int
    assert "writing" in binds.values()


# ─── list_accessible ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_accessible_public_and_own_only(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    uid = uuid4()
    fake_session.rows = [Skills(id=1, name="Public", is_public=True)]
    rows = await repo.list_accessible(uid)
    assert len(rows) == 1

    sql, binds = fake_session.calls[-1]
    assert "status =" in sql
    assert "is_public" in sql
    assert uid in binds.values()  # created_by == user_id bind (native uuid)
    # No team/project widening → no IN clause.
    assert "IN" not in sql


@pytest.mark.asyncio
async def test_list_accessible_team_and_project_widen_or(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    uid = uuid4()
    fake_session.rows = []
    await repo.list_accessible(uid, team_ids=[10, 20], project_ids=[30])

    sql, binds = fake_session.calls[-1]
    assert "project_id IN" in sql
    assert "team_id IN" in sql
    assert [30] in binds.values()
    assert [10, 20] in binds.values()


@pytest.mark.asyncio
async def test_list_accessible_legacy_project_id_merges(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    uid = uuid4()
    fake_session.rows = []
    await repo.list_accessible(uid, project_id=777)

    sql, binds = fake_session.calls[-1]
    assert "project_id IN" in sql
    assert [777] in binds.values()


# ─── list_binding_agents ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_binding_agents_sorted_by_name(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.tuples = [("beta", "Beta"), ("alpha", "Alpha")]
    agents = await repo.list_binding_agents(5)
    assert agents == [
        {"slug": "alpha", "name": "Alpha"},
        {"slug": "beta", "name": "Beta"},
    ]

    sql, binds = fake_session.calls[-1]
    assert "agent_skills" in sql and "ai_agents" in sql
    assert 5 in binds.values()


@pytest.mark.asyncio
async def test_list_binding_agents_skips_incomplete_rows(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.tuples = [("a", None), (None, "B"), ("c", "C")]
    agents = await repo.list_binding_agents(5)
    assert agents == [{"slug": "c", "name": "C"}]


# ─── list_files / get_file ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_files_ordered_and_uuid_str_parity(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fid = uuid.uuid4()
    fake_session.rows = [SkillFiles(id=fid, skill_id=10, path="SKILL.md", sort_order=0)]
    files = await repo.list_files(10)
    assert len(files) == 1
    assert files[0]["path"] == "SKILL.md"
    assert type(files[0]["id"]) is str  # uuid → str
    assert files[0]["id"] == str(fid)
    assert type(files[0]["skill_id"]) is int  # bigint → native int

    sql, binds = fake_session.calls[-1]
    assert "skill_files" in sql
    assert "ORDER BY" in sql and "sort_order" in sql and "path" in sql
    assert 10 in binds.values()


@pytest.mark.asyncio
async def test_get_file_returns_none_for_unknown_path(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    assert await repo.get_file(10, "missing.md") is None

    sql, binds = fake_session.calls[-1]
    assert 10 in binds.values()
    assert "missing.md" in binds.values()


@pytest.mark.asyncio
async def test_get_file_returns_row_when_found(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [
        SkillFiles(
            id=uuid.uuid4(),
            skill_id=10,
            path="SKILL.md",
            content="hi",
            file_type="markdown",
        )
    ]
    row = await repo.get_file(10, "SKILL.md")
    assert row is not None and row["content"] == "hi"


# ─── upsert_file ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_file_on_conflict_do_update(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [
        SkillFiles(
            id=uuid.uuid4(),
            skill_id=10,
            path="SKILL.md",
            content="hello",
            file_type="markdown",
        )
    ]
    result = await repo.upsert_file(
        10, path="SKILL.md", content="hello", file_type="markdown"
    )
    assert result["path"] == "SKILL.md"

    sql, binds = fake_session.calls[-1]
    assert "INSERT INTO public.skill_files" in sql
    assert "ON CONFLICT" in sql and "DO UPDATE" in sql
    assert "RETURNING" in sql
    assert 10 in binds.values()
    assert "SKILL.md" in binds.values()
    assert "hello" in binds.values()


@pytest.mark.asyncio
async def test_upsert_file_raises_when_no_data_returned(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    """Writes never silently no-op — empty result must raise."""
    fake_session.rows = []
    with pytest.raises(RuntimeError):
        await repo.upsert_file(10, path="x.md", content="", file_type="markdown")


# ─── delete_file ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_file_filters_by_skill_and_path(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    await repo.delete_file(10, "refs/old.md")

    sql, binds = fake_session.calls[-1]
    assert sql.startswith("DELETE FROM public.skill_files")
    assert 10 in binds.values()
    assert "refs/old.md" in binds.values()


# ─── insert / delete (skills) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_returns_row_and_raises_on_empty(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [Skills(id=99, slug="new", name="New")]
    created = await repo.insert({"slug": "new", "name": "New", "is_public": False})
    assert created["id"] == 99

    sql, binds = fake_session.calls[-1]
    assert "INSERT INTO public.skills" in sql
    assert "RETURNING" in sql
    assert "new" in binds.values()

    # empty result → RuntimeError
    fake_session.rows = []
    with pytest.raises(RuntimeError):
        await repo.insert({"slug": "x", "name": "X"})


@pytest.mark.asyncio
async def test_insert_filters_unmapped_keys(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [Skills(id=1, slug="s", name="S")]
    await repo.insert({"slug": "s", "name": "S", "not_a_column": "drop me"})

    _, binds = fake_session.calls[-1]
    assert "drop me" not in binds.values()  # unmapped key filtered out


@pytest.mark.asyncio
async def test_delete_skill_by_id(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    await repo.delete(99)

    sql, binds = fake_session.calls[-1]
    assert sql.startswith("DELETE FROM public.skills")
    assert 99 in binds.values()


# ─── update_fields / create / update ──────────────────────────────────


@pytest.mark.asyncio
async def test_update_fields_returns_row(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [Skills(id=10, name="Renamed")]
    result = await repo.update_fields(10, {"name": "Renamed"})
    assert result["name"] == "Renamed"

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.skills SET" in sql
    assert "RETURNING" in sql
    assert "Renamed" in binds.values()
    assert 10 in binds.values()


@pytest.mark.asyncio
async def test_update_fields_empty_after_filter_returns_empty_dict(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    # only unmapped keys → nothing to update → {} and no DB hit
    assert await repo.update_fields(10, {"not_a_column": "x"}) == {}
    assert fake_session.calls == []


@pytest.mark.asyncio
async def test_base_update_and_archive_route_through_orm(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    # archive() → self.update(id, {"status": "archived"}) → ORM UPDATE
    fake_session.rows = [Skills(id=10, status="archived")]
    await repo.archive(10)

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.skills SET" in sql
    assert "archived" in binds.values()
    assert 10 in binds.values()


@pytest.mark.asyncio
async def test_base_create_returns_row(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [Skills(id=5, name="C")]
    created = await repo.create({"name": "C", "slug": "c"})
    assert created["id"] == 5

    sql, _ = fake_session.calls[-1]
    assert "INSERT INTO public.skills" in sql and "RETURNING" in sql


# ─── update_fields_versioned (snapshot-then-update) ───────────────────


@pytest.mark.asyncio
async def test_update_fields_versioned_snapshots_body_and_bumps(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    """body_md change → INSERT snapshot (old body, v1) + UPDATE live row (v2)."""
    caller = uuid4()
    # 1st execute: SELECT current → the live row; snapshot INSERT + UPDATE follow.
    fake_session.queue(
        rows=[
            Skills(
                id=42,
                body_md="OLD body",
                frontmatter_json={"name": "Old"},
                current_version=1,
            )
        ]
    )
    await repo.update_fields_versioned(42, {"body_md": "NEW body"}, created_by=caller)

    # SELECT, INSERT skill_versions, UPDATE skills.
    assert len(fake_session.calls) == 3
    select_sql, _ = fake_session.calls[0]
    assert select_sql.startswith("SELECT")

    ins_sql, ins_binds = fake_session.calls[1]
    assert "INSERT INTO skill_versions" in ins_sql
    assert ins_binds["version_number"] == 1  # pre-bump
    assert ins_binds["body_md"] == "OLD body"
    assert ins_binds["created_by"] == str(caller)

    upd_sql, upd_binds = fake_session.calls[2]
    assert "UPDATE public.skills SET" in upd_sql
    assert "NEW body" in upd_binds.values()
    assert 2 in upd_binds.values()  # current_version bumped to 2


@pytest.mark.asyncio
async def test_update_fields_versioned_noop_when_untracked(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    """Only non-tracked field (name) differs → SELECT only, no snapshot/UPDATE."""
    fake_session.queue(
        rows=[Skills(id=7, body_md="same", frontmatter_json={}, current_version=5)]
    )
    await repo.update_fields_versioned(7, {"name": "Renamed"}, created_by=None)

    assert len(fake_session.calls) == 1  # only the current-fetch SELECT
    assert fake_session.calls[0][0].startswith("SELECT")


@pytest.mark.asyncio
async def test_update_fields_versioned_missing_raises(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []  # SELECT current → None
    with pytest.raises(ValueError, match="not found"):
        await repo.update_fields_versioned(1, {"body_md": "x"})


# ─── upsert_file_versioned (3 paths) ──────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_file_versioned_new_file_inserts_v1(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    """No existing file → INSERT with current_version=1, no snapshot."""
    new_id = uuid.uuid4()
    fake_session.queue(rows=[])  # SELECT current → None
    fake_session.queue(
        rows=[
            SkillFiles(
                id=new_id,
                skill_id=7,
                path="scripts/validate.py",
                content="print('hi')",
                file_type="script",
                current_version=1,
            )
        ]
    )
    row = await repo.upsert_file_versioned(
        skill_id=7,
        path="scripts/validate.py",
        content="print('hi')",
        file_type="script",
    )
    assert row["current_version"] == 1
    assert type(row["id"]) is str

    # SELECT then INSERT skill_files (no skill_file_versions snapshot).
    assert len(fake_session.calls) == 2
    assert fake_session.calls[0][0].startswith("SELECT")
    assert "INSERT INTO public.skill_files" in fake_session.calls[1][0]


@pytest.mark.asyncio
async def test_upsert_file_versioned_existing_snapshots_and_bumps(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    """Existing file, changed content → snapshot old + UPDATE → v2."""
    fid = uuid.uuid4()
    fake_session.queue(
        rows=[
            SkillFiles(
                id=fid,
                skill_id=7,
                path="references/examples.md",
                content="OLD content",
                file_type="markdown",
                binary_url=None,
                current_version=2,
            )
        ]
    )
    fake_session.queue()  # text() INSERT snapshot (no return consumed)
    fake_session.queue(
        rows=[
            SkillFiles(
                id=fid,
                skill_id=7,
                path="references/examples.md",
                content="NEW content",
                file_type="markdown",
                current_version=3,
            )
        ]
    )
    result = await repo.upsert_file_versioned(
        skill_id=7,
        path="references/examples.md",
        content="NEW content",
        file_type="markdown",
    )
    assert result["current_version"] == 3
    assert result["content"] == "NEW content"

    assert len(fake_session.calls) == 3
    snap_sql, snap_binds = fake_session.calls[1]
    assert "INSERT INTO skill_file_versions" in snap_sql
    assert snap_binds["skill_file_id"] == str(fid)  # uuid → str bind
    assert snap_binds["version_number"] == 2
    assert snap_binds["content"] == "OLD content"

    upd_sql, upd_binds = fake_session.calls[2]
    assert "UPDATE public.skill_files SET" in upd_sql
    assert "NEW content" in upd_binds.values()
    assert 3 in upd_binds.values()


@pytest.mark.asyncio
async def test_upsert_file_versioned_noop_when_unchanged(
    repo: SkillRepository, fake_session: _FakeSession
) -> None:
    """Existing file, same content → no snapshot, no update, returns current."""
    fake_session.queue(
        rows=[
            SkillFiles(
                id=uuid.uuid4(),
                skill_id=7,
                path="references/examples.md",
                content="same",
                file_type="markdown",
                binary_url=None,
                current_version=1,
            )
        ]
    )
    result = await repo.upsert_file_versioned(
        skill_id=7,
        path="references/examples.md",
        content="same",
        file_type="markdown",
    )
    assert result["current_version"] == 1
    assert len(fake_session.calls) == 1  # only the current-fetch SELECT
