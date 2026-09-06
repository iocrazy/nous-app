"""Guard for migration 456 — one inbox row per promoted resource.

Two artefacts have to agree and neither can see the other: the SQL that ships
to Postgres, and the ``Index`` in the ORM model that tells the next reader the
constraint exists. ``tests/db/test_schema_drift.py`` compares columns, not
indexes, so nothing else would notice one of them being edited alone.

Pure text + metadata assertions — no database. The behaviour under concurrency
(loser of the advisory lock FINDS the winner's row rather than colliding with
this index) is the repository's contract, tested in ``tests/repositories``.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Index

from app.models.generated_media import GeneratedMedia

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "456_generated_media_promoted_unique.sql"
)

_INDEX_NAME = "uq_genmedia_promoted_resource"


def _sql() -> str:
    assert _MIGRATION.exists(), f"missing migration: {_MIGRATION}"
    return _MIGRATION.read_text(encoding="utf-8")


def test_migration_creates_the_partial_unique_index():
    sql = _sql()
    assert f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME}" in sql
    assert "WHERE promoted_resource_id IS NOT NULL" in sql


def test_migration_drops_the_subsumed_plain_index():
    assert "DROP INDEX IF EXISTS public.idx_genmedia_promoted" in _sql()


def _orm_index() -> Index:
    matches = [ix for ix in GeneratedMedia.__table__.indexes if ix.name == _INDEX_NAME]
    assert matches, (
        f"{_INDEX_NAME} is in migration 456 but not in "
        "GeneratedMedia.__table_args__ — the two have drifted"
    )
    return matches[0]


def test_orm_mirrors_the_index_as_unique():
    assert _orm_index().unique is True


def test_orm_mirrors_the_partial_predicate():
    predicate = _orm_index().dialect_options["postgresql"]["where"]
    assert str(predicate) == "promoted_resource_id IS NOT NULL"
