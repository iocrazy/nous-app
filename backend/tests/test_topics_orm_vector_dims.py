"""Pin the topics ORM vector widths to the physical columns.

Sibling of ``test_analysis_repository_vector_binds.py``. pgvector's SQLAlchemy
type validates dimensions at *bind* time, so an ORM ``Vector(n)`` narrower
than the column is a silent write-killer: every ORM insert raises
``ValueError: expected n dimensions, not 2048`` and callers read that as
"no vector, skip". ``topic_groups`` sat at ``Vector(1536)`` from migration
314 (which widened the column to 2048) until 2026-09-22 — latent only because
``topic_groups_repository`` inserts via raw SQL and nothing wrote through the
model. This test makes the next drift loud at unit-test time, without a DB.
"""

from __future__ import annotations

import inspect

from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from app.models import topics

# table name -> dimension declared by the last migration that touched the
# column (304 created topic_groups at 1536; 314 widened it to 2048; 315
# created user_topic_interests at 2048; hotspots followed the same embedder).
EXPECTED_DIMS = {
    "hotspots": 2048,
    "topic_groups": 2048,
    "user_topic_interests": 2048,
}


def _vector_columns() -> dict[str, Vector]:
    found: dict[str, Vector] = {}
    for _name, obj in inspect.getmembers(topics, inspect.isclass):
        table = getattr(obj, "__table__", None)
        if table is None or table.name not in EXPECTED_DIMS:
            continue
        col = table.c.get("embedding")
        if col is not None and isinstance(col.type, Vector):
            found[table.name] = col.type
    return found


def test_every_expected_table_has_an_orm_vector_column() -> None:
    assert sorted(_vector_columns()) == sorted(EXPECTED_DIMS)


def test_orm_vector_dims_match_the_migrated_columns() -> None:
    dialect = postgresql.dialect()
    for table, vtype in _vector_columns().items():
        expected = EXPECTED_DIMS[table]
        assert (
            vtype.dim == expected
        ), f"{table}.embedding: ORM {vtype.dim} != column {expected}"
        # Bind a vector of the column's real width — must not raise.
        vtype.bind_processor(dialect)([0.0] * expected)
