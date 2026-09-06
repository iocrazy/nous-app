"""``resources.source_type`` has ONE value set, declared in three places that
cannot see each other: the DB CHECK (a migration), the ORM CheckConstraint
(``app/models/media.py``) and the router allowlists. The model declared
``('web','upload')`` for a year after migrations 309/363 widened the DB to
four values — declarative-only, so nothing failed until a filter depended on
the wider set. This test reads the LATEST migration that (re)creates the
constraint and refuses a drift in either direction.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import CheckConstraint

from app.api.resources_crud_router import _ALLOWED_SOURCE_TYPES
from app.models.media import RESOURCE_SOURCE_TYPES, Resources

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"

_ADD = re.compile(
    r"ADD\s+CONSTRAINT\s+resources_source_type_check\s+CHECK\s*\(\s*source_type\s+IN\s*\(([^)]*)\)",
    re.IGNORECASE,
)


def _latest_migration_values() -> tuple[int, set[str]]:
    """The value set from the highest-numbered migration that re-adds the
    constraint. Highest number, not last-in-file-order: 309 and 363 both add
    it, and only the later one is what the live DB holds."""
    found: list[tuple[int, set[str]]] = []
    for sql in sorted(MIGRATIONS.glob("*.sql")):
        m = _ADD.search(sql.read_text(encoding="utf-8"))
        if not m:
            continue
        number = int(sql.name.split("_", 1)[0])
        values = {v.strip().strip("'") for v in m.group(1).split(",")}
        found.append((number, values))
    assert (
        found
    ), "no migration adds resources_source_type_check — the regex or the tree drifted"
    return max(found, key=lambda t: t[0])


def test_model_constant_mirrors_the_latest_migration():
    number, values = _latest_migration_values()
    assert number >= 363, f"expected 363 or later to own the constraint, found {number}"
    assert set(RESOURCE_SOURCE_TYPES) == values


def test_orm_check_constraint_is_rendered_from_the_constant():
    checks = [
        c for c in Resources.__table__.constraints if isinstance(c, CheckConstraint)
    ]
    check = next(c for c in checks if c.name == "resources_source_type_check")
    sql = str(check.sqltext)
    for value in RESOURCE_SOURCE_TYPES:
        assert f"'{value}'::character varying::text" in sql
    # Falsifiable in the other direction too: nothing outside the constant.
    assert len(re.findall(r"'([a-z_]+)'::character varying::text", sql)) == len(
        RESOURCE_SOURCE_TYPES
    )


def test_router_allowlists_are_the_same_set():
    assert _ALLOWED_SOURCE_TYPES == set(RESOURCE_SOURCE_TYPES)
