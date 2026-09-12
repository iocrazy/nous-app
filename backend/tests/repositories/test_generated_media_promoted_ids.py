"""generation id → promoted resource id, for the canvas refs mirror."""

from __future__ import annotations

import contextlib

from sqlalchemy.dialects import postgresql

from app.repositories import generated_media_repository as gmr


def test_statement_reads_only_promoted_rows_for_the_ids() -> None:
    sql = str(
        gmr._promoted_resource_ids_stmt([5, 6], 77).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "generated_media.id IN (5, 6)" in sql
    assert "generated_media.promoted_resource_id IS NOT NULL" in sql
    # A purged resource leaves promoted_resource_id dangling (no FK); only ids
    # that still exist may reach canvas_resource_refs, whose FK would reject
    # the whole replace and wipe every ref the canvas has.
    assert (
        "JOIN public.resources ON public.resources.id = "
        "public.generated_media.promoted_resource_id" in sql
    )


def test_statement_only_admits_resources_filed_in_the_canvas_scope() -> None:
    # The gen ids come from nodes_json, which a canvas member writes. Without
    # this join, pasting another tenant's generation id into a node would file
    # THAT tenant's resource id into this canvas's refs mirror.
    sql = str(
        gmr._promoted_resource_ids_stmt([5], 77).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "resource_items" in sql
    assert "public.resource_items.resource_id = public.resources.id" in sql
    assert "public.resource_items.scope_id = 77" in sql


async def test_no_ids_never_touch_the_database(monkeypatch) -> None:
    def refuse():
        raise AssertionError("queried with no ids")

    monkeypatch.setattr(gmr, "read_scope", refuse)
    assert (
        await gmr.GeneratedMediaRepository().promoted_resource_ids([], scope_id=77)
        == {}
    )


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
    got = await gmr.GeneratedMediaRepository().promoted_resource_ids(
        ["5", 7, 5], scope_id=77
    )
    assert got == {5: 222, 7: 333}
    [stmt] = executed
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "IN (5, 7)" in sql


def _fake_read_scope(entered: list[str], executed: list):
    class _Result:
        def all(self):
            return [(5, 222)]

    class _Session:
        async def execute(self, stmt):
            executed.append(list(entered))
            return _Result()

    @contextlib.asynccontextmanager
    async def fake_read_scope():
        yield _Session()

    return fake_read_scope


async def test_enforced_resources_read_runs_under_system_scope(monkeypatch) -> None:
    # Resources is UserScoped: under an ambient user scope the join would
    # silently drop another contributor's archived output.
    entered: list[str] = []
    executed: list = []
    reasons: list[str] = []

    @contextlib.asynccontextmanager
    async def fake_system_scope(reason: str):
        reasons.append(reason)
        entered.append("system")
        try:
            yield
        finally:
            entered.remove("system")

    monkeypatch.setattr(gmr, "is_enforced", lambda table: table == "resources")
    monkeypatch.setattr(gmr, "system_request_scope", fake_system_scope)
    monkeypatch.setattr(gmr, "read_scope", _fake_read_scope(entered, executed))
    got = await gmr.GeneratedMediaRepository().promoted_resource_ids([5], scope_id=77)
    assert got == {5: 222}
    assert executed == [["system"]]
    assert len(reasons) == 1 and reasons[0]


async def test_unenforced_resources_read_skips_the_system_scope(monkeypatch) -> None:
    entered: list[str] = []
    executed: list = []

    def refuse(reason: str):
        raise AssertionError("system scope opened while resources unenforced")

    monkeypatch.setattr(gmr, "is_enforced", lambda table: False)
    monkeypatch.setattr(gmr, "system_request_scope", refuse)
    monkeypatch.setattr(gmr, "read_scope", _fake_read_scope(entered, executed))
    assert await gmr.GeneratedMediaRepository().promoted_resource_ids(
        [5], scope_id=77
    ) == {5: 222}
    assert executed == [[]]
