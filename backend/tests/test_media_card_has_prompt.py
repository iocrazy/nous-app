"""Card projection ``has_prompt`` overlay — media_list / search parity.

The library card renders a Prompt icon next to the audio / transcript /
summary / analysis ones, so the card-view projection has to say whether the
asset carries a prompt. The prompt text itself lives on ``resources``
(``gen_prompt`` / ``gen_prompt_zh`` / ``slide_prompts``) and is capped at
20 000 chars per column, so the projection carries a computed BOOLEAN
(``has_prompt_expr``) instead of the text.

These are no-DB tests: ``read_scope`` is replaced with a fake session that
captures the emitted statement and returns canned rows, so both the SQL shape
and the returned dict shape are asserted without a live database (the pattern
``tests/test_admin_search_repository.py`` established). ``get_user_media_list``
and ``search`` share one projection helper — each is pinned separately so a
future edit to one shows up as a failure rather than as drift.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.media_repository as mod
from app.repositories.media_repository import MediaRepository, has_prompt_expr


class _AttrRow:
    """Attribute-access stand-in for a ``ParsedMedia`` ORM row; unknown
    attributes read as None so a partial fixture survives the CARD_SELECT
    projection."""

    def __init__(self, **data: Any) -> None:
        self.__dict__.update(data)

    def __getattr__(self, name: str) -> Any:
        return None


class _FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def all(self) -> list[tuple]:
        return self._rows


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.rows: list[tuple] = []

    async def execute(self, stmt: Any, binds: dict[str, Any] | None = None) -> Any:
        self.calls.append(stmt)
        return _FakeResult(self.rows)


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
    return session


@pytest.fixture
def repo() -> MediaRepository:
    return MediaRepository()


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


# ─── The expression itself ──────────────────────────────────────────


def test_has_prompt_expr_covers_both_texts_and_the_slide_map() -> None:
    sql = _sql(has_prompt_expr().self_group())
    assert "resources.gen_prompt" in sql
    assert "resources.gen_prompt_zh" in sql
    assert "resources.slide_prompts" in sql


def test_has_prompt_expr_trims_so_a_cleared_prompt_is_not_a_prompt() -> None:
    """The prompt editor writes ``''`` (not NULL) when a field is cleared, so a
    bare NOT NULL test would light the icon on an empty prompt."""
    sql = _sql(has_prompt_expr().self_group())
    assert "btrim" in sql
    assert "coalesce" in sql.lower()


def test_has_prompt_expr_ignores_negative_prompts() -> None:
    """A negative-only asset has no prompt to show."""
    sql = _sql(has_prompt_expr().self_group())
    assert "gen_prompt_negative" not in sql


def test_has_prompt_expr_rejects_an_empty_slide_map() -> None:
    sql = _sql(has_prompt_expr().self_group())
    assert "IS NOT NULL" in sql
    assert "JSONB" in sql.upper()


def test_has_prompt_expr_returns_a_fresh_clause_each_call() -> None:
    """A shared module constant would be re-parented across statements."""
    assert has_prompt_expr() is not has_prompt_expr()


# ─── get_user_media_list ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_media_list_selects_and_returns_has_prompt(
    repo: MediaRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [(555, True, _AttrRow(id=1, platform_id="pid1"))]
    rows = await repo.get_user_media_list("u1")

    assert len(rows) == 1
    assert rows[0]["has_prompt"] is True
    assert rows[0]["resource_id"] == 555
    assert rows[0]["platform_id"] == "pid1"

    sql = _sql(fake_session.calls[0])
    assert "__has_prompt" in sql
    assert "resources.gen_prompt" in sql


@pytest.mark.asyncio
async def test_media_list_has_prompt_false_stays_false(
    repo: MediaRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [(555, False, _AttrRow(id=1, platform_id="pid1"))]
    rows = await repo.get_user_media_list("u1")
    assert rows[0]["has_prompt"] is False


@pytest.mark.asyncio
async def test_media_list_has_prompt_is_always_a_bool(
    repo: MediaRepository, fake_session: _FakeSession
) -> None:
    """asyncpg hands PG booleans back as bool, but a NULL from a LEFT-joined
    row would leak None into the JSON payload — the frontend treats the field
    as a boolean, so the overlay coerces."""
    fake_session.rows = [(555, None, _AttrRow(id=1, platform_id="pid1"))]
    rows = await repo.get_user_media_list("u1")
    assert rows[0]["has_prompt"] is False


@pytest.mark.asyncio
async def test_media_list_keeps_card_projection_free_of_prompt_text(
    repo: MediaRepository, fake_session: _FakeSession
) -> None:
    """``has_prompt`` is an overlay — the 20 000-char prompt columns must not
    ride along in the card payload."""
    fake_session.rows = [(555, True, _AttrRow(id=1, platform_id="pid1"))]
    rows = await repo.get_user_media_list("u1")
    assert "gen_prompt" not in rows[0]
    assert "slide_prompts" not in rows[0]


# ─── search ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_returns_the_same_card_shape_as_media_list(
    repo: MediaRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [(777, True, _AttrRow(id=2, platform_id="pid2"))]
    search_rows = await repo.search("u1", keyword="cat")

    fake_session.calls.clear()
    fake_session.rows = [(777, True, _AttrRow(id=2, platform_id="pid2"))]
    list_rows = await repo.get_user_media_list("u1")

    assert search_rows[0].keys() == list_rows[0].keys()
    assert search_rows[0]["has_prompt"] is True
    assert search_rows[0]["resource_id"] == 777


@pytest.mark.asyncio
async def test_search_selects_has_prompt(
    repo: MediaRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.search("u1", keyword="cat")
    sql = _sql(fake_session.calls[0])
    assert "__has_prompt" in sql
    assert "resources.slide_prompts" in sql
