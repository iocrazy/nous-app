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


def test_statement_keeps_out_of_scope_rows_visible_as_a_flag() -> None:
    """The scope join is a LEFT JOIN carrying an ``in_scope`` flag.

    An INNER JOIN makes "filed in another scope" indistinguishable from "never
    promoted" and from "the resource was purged" — the row simply is not there,
    and the caller drops the ref with nothing to say. Promotes from before
    #2212 filed canvas outputs in the CALLER's personal team, so such rows are
    real, legitimate, and now out of the canvas's scope: they must be
    reportable, not invisible.
    """
    sql = str(
        gmr._promoted_resource_ids_stmt([5], 77).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "LEFT OUTER JOIN public.resource_items" in sql
    assert "in_scope" in sql


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
            return [(5, 222, True), (7, 333, True)]

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


def _rows_read_scope(rows: list[tuple]):
    class _Result:
        def all(self):
            return rows

    class _Session:
        async def execute(self, stmt):
            return _Result()

    @contextlib.asynccontextmanager
    async def fake_read_scope():
        yield _Session()

    return fake_read_scope


async def test_out_of_scope_rows_are_dropped_and_reported(monkeypatch) -> None:
    """Dropping a ref is a real outcome of a save, so it gets a line.

    A resource promoted before #2212 sits in the promoter's PERSONAL scope even
    though the canvas is a team's, and the canvas has shown it ever since. It is
    right to stop mirroring it — and wrong to do that silently: "the ref
    vanished on save" with nothing in the log is indistinguishable from a bug in
    the extractor, which is how this class of change eats a week.
    """
    from loguru import logger as loguru_logger

    seen: list[str] = []
    handler_id = loguru_logger.add(lambda m: seen.append(str(m)), level="WARNING")
    monkeypatch.setattr(gmr, "is_enforced", lambda table: False)
    monkeypatch.setattr(
        gmr,
        "read_scope",
        _rows_read_scope([(5, 222, True), (7, 333, False), (9, 444, False)]),
    )
    try:
        got = await gmr.GeneratedMediaRepository().promoted_resource_ids(
            [5, 7, 9], scope_id=77
        )
    finally:
        loguru_logger.remove(handler_id)

    assert got == {5: 222}, "only the in-scope pair may reach the refs mirror"
    assert len(seen) == 1, f"exactly one line per call, not one per row: {seen}"
    line = seen[0]
    assert "2" in line, f"the COUNT of dropped refs must be in the line: {line}"
    assert "7" in line and "9" in line, (
        f"the dropped generation ids must be named — without them nobody can "
        f"tell WHICH picture stopped being mirrored: {line}"
    )
    assert "77" in line, f"the scope that was asked about: {line}"


async def test_a_fully_in_scope_call_says_nothing(monkeypatch) -> None:
    """Negative control. A warning on every save is a warning nobody reads,
    and it would make the pin above pass for the wrong reason."""
    from loguru import logger as loguru_logger

    seen: list[str] = []
    handler_id = loguru_logger.add(lambda m: seen.append(str(m)), level="WARNING")
    monkeypatch.setattr(gmr, "is_enforced", lambda table: False)
    monkeypatch.setattr(gmr, "read_scope", _rows_read_scope([(5, 222, True)]))
    try:
        assert await gmr.GeneratedMediaRepository().promoted_resource_ids(
            [5], scope_id=77
        ) == {5: 222}
    finally:
        loguru_logger.remove(handler_id)
    assert seen == []


def _fake_read_scope(entered: list[str], executed: list):
    class _Result:
        def all(self):
            return [(5, 222, True)]

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
