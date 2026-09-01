"""Compiled-SQL pins for ``AssetRelationsRepository``'s two locking SELECTs.

Both run inside a ``write_scope()`` against a session the unit suite stubs, so
what they actually ask Postgres for is otherwise only exercised by
``tests/db/test_assets_repository_integration.py`` (skipped without a DB).
Compiling against the real PostgreSQL dialect is the part that runs anywhere.

What is being pinned is a CONCURRENCY property, and concurrency properties do
not fail in a way a functional test notices: an unordered ``FOR UPDATE`` scan
returns the right rows every single time you look, and only deadlocks when two
transactions happen to interleave. There is no assertion about behaviour that
would go red — so the ORDER BY is pinned at the statement itself.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.asset_relations_repository import AssetRelationsRepository

ASSET = 727145299382534200
LOADOUT = 727145299382534201


def _sql(stmt) -> str:
    return " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())


@pytest.mark.parametrize(
    "stmt",
    [
        AssetRelationsRepository._owned_probe_stmt(LOADOUT, ASSET),
        AssetRelationsRepository._siblings_for_update_stmt(ASSET),
    ],
    ids=["set_default_probe", "strip_from_loadouts_scan"],
)
def test_every_locking_select_takes_its_rows_in_id_order(stmt):
    """A total order shared by every locker is what makes a lock cycle
    unconstructible. Both halves of the assertion matter: ORDER BY without FOR
    UPDATE locks nothing, FOR UPDATE without ORDER BY is the deadlock."""
    sql = _sql(stmt)
    assert "FOR UPDATE" in sql
    assert "ORDER BY public.asset_loadouts.id" in sql
    # The ordering must be applied BEFORE rows are locked, i.e. it is part of
    # this statement rather than a sort the caller does afterwards.
    assert sql.index("ORDER BY") < sql.index("FOR UPDATE")


def test_the_two_lockers_order_by_the_same_column():
    """Two lockers each ordered by a DIFFERENT column is the same deadlock with
    extra steps."""
    a = _sql(AssetRelationsRepository._owned_probe_stmt(LOADOUT, ASSET))
    b = _sql(AssetRelationsRepository._siblings_for_update_stmt(ASSET))
    key = "ORDER BY public.asset_loadouts.id FOR UPDATE"
    assert a.endswith(key) and b.endswith(key)


def test_no_other_locking_select_escaped_the_two_builders():
    """The guarantee is a property of the WHOLE set of lockers: one unordered
    straggler restores the deadlock for everybody. So this asserts on the
    source file, not on the two statements above — a new ``with_for_update()``
    written inline (the shape both of these had before) fails here."""
    import inspect

    from app.repositories import asset_relations_repository as mod

    src = inspect.getsource(mod)
    # The two builders are the only places the lock is taken. Comments naming
    # `with_for_update()` are stripped first so the module's own documentation
    # cannot satisfy — or trip — the count.
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert code.count("with_for_update()") == 2
