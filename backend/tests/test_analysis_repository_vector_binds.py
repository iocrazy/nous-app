"""Pins for the vector RPC call (2026-09-15, found by the outside-model pass).

Both defects sat on master unexecuted because production had zero vectors:
the first real embedding would have hit them together.
"""

from __future__ import annotations

import inspect
import re

from sqlalchemy import text


def _rpc_sql() -> str:
    from app.repositories import analysis_repository as m

    src = inspect.getsource(m.AnalysisRepository.search_by_embedding)
    # The statement is built from adjacent string literals; join them.
    parts = re.findall(
        r'"([^"\n]*match_videos_by_embedding[^"\n]*|CAST\([^"\n]*)"', src
    )
    return " ".join(parts)


def test_every_rpc_argument_is_a_bound_parameter() -> None:
    """``:t::double precision`` is NOT a bind to SQLAlchemy (a name followed by
    ``::`` is skipped), so Postgres received literal ``:t`` and raised a
    syntax error. Every argument must use ``CAST(:name AS type)``."""
    sql = _rpc_sql()
    assert "match_videos_by_embedding" in sql
    assert "::" not in sql, sql
    assert sorted(text(sql)._bindparams.keys()) == ["c", "m", "q", "t", "u"]


def test_orm_vector_dimension_matches_the_configured_embedder() -> None:
    """pgvector validates dimensions at bind time; a stale ``Vector(1536)``
    raised ``ValueError: expected 1536 dimensions, not 2048`` on every write
    of a doubao-embedding-vision vector (the column is vector(2048) since
    migration 315)."""
    from pgvector.sqlalchemy import Vector
    from sqlalchemy.dialects import postgresql

    from app.models.media import ResourceAnalysis

    col = ResourceAnalysis.__table__.c.content_embedding
    assert isinstance(col.type, Vector) and col.type.dim == 2048
    col.type.bind_processor(postgresql.dialect())([0.0] * 2048)  # must not raise
