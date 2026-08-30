"""``AssetsRepository.count_by_type`` — the zero-fill, without a database.

The SELECT's predicates are pinned by compiled-SQL assertions in
``tests/services/assets/test_assets_repository_sql.py`` and executed for real
in ``tests/db/test_assets_repository_integration.py`` (case 16). What is left
over — and what those two cannot see — is what this method does with the rows
Postgres hands back: a GROUP BY answers only for types that HAVE rows, so
folding it into a six-key dict is Python's job, and getting it wrong ships a
sidebar where "none yet" and "no such type" look identical.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.models.assets import ASSET_TYPES
from app.repositories import assets_repository as repo_mod
from app.repositories.assets_repository import AssetsRepository


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._rows)


@pytest.fixture
def fake_read_scope(monkeypatch):
    """Swap ``read_scope`` for a session that replays a fixed GROUP BY result."""
    holder = {}

    def install(rows):
        session = _Session(rows)
        holder["session"] = session

        @asynccontextmanager
        async def _scope():
            yield session

        monkeypatch.setattr(repo_mod, "read_scope", _scope)
        return session

    install.holder = holder
    return install


@pytest.mark.asyncio
async def test_types_with_no_rows_come_back_as_zero(fake_read_scope):
    fake_read_scope([("character", 3), ("prompt", 1)])

    out = await AssetsRepository().count_by_type(9000)

    assert set(out) == set(ASSET_TYPES)
    assert out["character"] == 3 and out["prompt"] == 1
    assert out["location"] == out["prop"] == out["costume"] == out["audio"] == 0


@pytest.mark.asyncio
async def test_an_empty_scope_is_six_zeros_not_an_empty_dict(fake_read_scope):
    """A brand-new team gets no GROUP BY rows at all. ``{}`` would make every
    badge disappear rather than read 0."""
    fake_read_scope([])

    assert await AssetsRepository().count_by_type(9000) == {t: 0 for t in ASSET_TYPES}


@pytest.mark.asyncio
async def test_counts_are_ints_even_when_the_driver_hands_back_decimals(
    fake_read_scope,
):
    """``count()`` comes back as an int on asyncpg, but the coercion is what
    keeps this method's contract independent of that."""
    fake_read_scope([("audio", "7")])

    out = await AssetsRepository().count_by_type(9000)

    assert out["audio"] == 7 and isinstance(out["audio"], int)


@pytest.mark.asyncio
async def test_a_type_outside_the_table_is_carried_not_dropped(fake_read_scope):
    """If the DB ever holds a type the code does not know, silently dropping it
    would under-report the library. Surfacing it is the honest answer — and it
    is how a reader of the badge notices the drift at all."""
    fake_read_scope([("character", 1), ("mystery", 4)])

    out = await AssetsRepository().count_by_type(9000)

    assert out["mystery"] == 4
    assert out["character"] == 1
