"""generation id → promoted resource id, for the canvas refs mirror."""

from __future__ import annotations

import contextlib

from sqlalchemy.dialects import postgresql

from app.repositories import generated_media_repository as gmr


def test_statement_reads_only_promoted_rows_for_the_ids() -> None:
    sql = str(
        gmr._promoted_resource_ids_stmt([5, 6]).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "generated_media.id IN (5, 6)" in sql
    assert "generated_media.promoted_resource_id IS NOT NULL" in sql


async def test_no_ids_never_touch_the_database(monkeypatch) -> None:
    def refuse():
        raise AssertionError("queried with no ids")

    monkeypatch.setattr(gmr, "read_scope", refuse)
    assert await gmr.GeneratedMediaRepository().promoted_resource_ids([]) == {}


async def test_rows_become_an_int_map_over_deduped_ids(monkeypatch) -> None:
    executed: list = []

    class _Result:
        def all(self):
            return [(5, 222), (7, 333)]

    class _Session:
        async def execute(self, stmt):
            executed.append(stmt)
            return _Result()

    @contextlib.asynccontextmanager
    async def fake_read_scope():
        yield _Session()

    monkeypatch.setattr(gmr, "read_scope", fake_read_scope)
    got = await gmr.GeneratedMediaRepository().promoted_resource_ids(["5", 7, 5])
    assert got == {5: 222, 7: 333}
    [stmt] = executed
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "IN (5, 7)" in sql
