"""Stubbed-session pins for the two mig-499 repositories.

What only a stub can pin cheaply: the statement SHAPE (CAST binds, ON
CONFLICT target, RPC name) and the error CLASSIFICATION (a missing table /
function becomes the typed ``EmbeddingStoreMissing``; every other
ProgrammingError re-raises). Whether Postgres accepts the statements is
``tests/db/test_migration_497_resource_embeddings_integration.py``'s job.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from pgvector import HalfVector
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import ProgrammingError

from app.core.embedding_space import SpaceSpec

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

REPO = "app.repositories.resource_embeddings_repository"
SPACE_REPO = "app.repositories.embedding_space_repository"
USER = "11111111-1111-1111-1111-111111111111"


# ── stub session ──────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> "_Result":
        return self

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        return self._scalar


class _Session:
    """Records every statement; answers from a queue of results, or raises."""

    def __init__(self, results: list[_Result] | None = None, raises=None) -> None:
        self.calls: list[tuple[Any, Any]] = []
        self._results = list(results or [])
        self._raises = raises

    async def execute(self, stmt, params=None):
        self.calls.append((stmt, params))
        if self._raises is not None:
            raise self._raises
        return self._results.pop(0) if self._results else _Result()


def _scope(session: _Session):
    @asynccontextmanager
    async def _cm():
        yield session

    return _cm


class _Unreachable:
    async def __aenter__(self):
        raise AssertionError("the session must not be reached")

    async def __aexit__(self, *exc):
        return False


def _pg_error(sqlstate: str) -> ProgrammingError:
    return ProgrammingError("stmt", {}, SimpleNamespace(sqlstate=sqlstate))


def _sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}
        )
    )


def _vec() -> list[float]:
    return [0.5] * 2048


# ── ResourceEmbeddingsRepository.search ───────────────────────────────────


async def test_search_calls_rpc_with_casts_and_space():
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    row = {
        "resource_id": 7,
        "media_id": 8,
        "platform_id": "p",
        "title": "t",
        "description": "d",
        "cover_urls": [],
        "author": "a",
        "view_count": 3,
        "created_at": datetime(2026, 9, 22, tzinfo=timezone.utc),
        "similarity": 0.9,
    }
    session = _Session([_Result([row])])
    with patch(f"{REPO}.read_scope", _scope(session)):
        out = await ResourceEmbeddingsRepository().search(
            embedding=[0.1, 0.2],
            space_id=42,
            layer="semantic",
            user_id=USER,
            limit=5,
            threshold=0.3,
        )

    stmt, params = session.calls[0]
    sql = str(stmt)
    assert "match_resource_embeddings(" in sql
    assert "CAST(:q AS halfvec)" in sql
    assert "::" not in sql, sql
    assert sorted(stmt._bindparams.keys()) == ["c", "l", "q", "s", "t", "u"]
    assert params == {
        "q": "[0.1,0.2]",
        "s": 42,
        "l": "semantic",
        "t": 0.3,
        "c": 5,
        "u": USER,
    }
    assert out == [{**row, "created_at": "2026-09-22T00:00:00+00:00", "resource_id": 7}]


async def test_search_translates_undefined_function_to_store_missing():
    from app.repositories.resource_embeddings_repository import (
        EmbeddingStoreMissing,
        ResourceEmbeddingsRepository,
    )

    session = _Session(raises=_pg_error("42883"))
    with patch(f"{REPO}.read_scope", _scope(session)):
        with pytest.raises(EmbeddingStoreMissing):
            await ResourceEmbeddingsRepository().search(
                embedding=[0.1],
                space_id=1,
                layer="semantic",
                user_id=USER,
                limit=5,
                threshold=0.3,
            )


async def test_search_refuses_falsy_user_id():
    """A NULL/empty owner would widen the RPC to every user's vectors."""
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    with patch(f"{REPO}.read_scope", lambda: _Unreachable()):
        with pytest.raises(ValueError):
            await ResourceEmbeddingsRepository().search(
                embedding=[0.1],
                space_id=1,
                layer="semantic",
                user_id="",
                limit=5,
                threshold=0.3,
            )


# ── upsert ────────────────────────────────────────────────────────────────


async def test_upsert_uses_on_conflict_do_update():
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    session = _Session()
    with patch(f"{REPO}.write_scope", _scope(session)):
        await ResourceEmbeddingsRepository().upsert(
            resource_id=7,
            layer="semantic",
            space_id=42,
            embedding=_vec(),
            source_hash="h",
            source_text="doc",
        )

    sql = _sql(session.calls[0][0])
    assert "ON CONFLICT (resource_id, layer, space_id) DO UPDATE" in sql
    set_clause = sql.split("DO UPDATE SET", 1)[1]
    for col in ("embedding", "source_hash", "source_text", "updated_at"):
        assert f"{col} =" in set_clause, set_clause
    assert "created_at =" not in set_clause


async def test_upsert_translates_undefined_table_to_store_missing():
    from app.repositories.resource_embeddings_repository import (
        EmbeddingStoreMissing,
        ResourceEmbeddingsRepository,
    )

    session = _Session(raises=_pg_error("42P01"))
    with patch(f"{REPO}.write_scope", _scope(session)):
        with pytest.raises(EmbeddingStoreMissing):
            await ResourceEmbeddingsRepository().upsert(
                resource_id=7,
                layer="semantic",
                space_id=42,
                embedding=_vec(),
                source_hash="h",
                source_text=None,
            )


async def test_upsert_reraises_other_programming_errors():
    """42703 (undefined column) says "does not exist" too — it is a real
    defect, not a deploy-order window, and must not be dressed up as one."""
    from app.repositories.resource_embeddings_repository import (
        EmbeddingStoreMissing,
        ResourceEmbeddingsRepository,
    )

    boom = _pg_error("42703")
    session = _Session(raises=boom)
    with patch(f"{REPO}.write_scope", _scope(session)):
        with pytest.raises(ProgrammingError) as info:
            await ResourceEmbeddingsRepository().upsert(
                resource_id=7,
                layer="semantic",
                space_id=42,
                embedding=_vec(),
                source_hash="h",
                source_text=None,
            )
    assert not isinstance(info.value, EmbeddingStoreMissing)
    assert info.value is boom


# ── get ───────────────────────────────────────────────────────────────────


async def test_get_returns_plain_float_list():
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    orm_row = SimpleNamespace(
        resource_id=7,
        layer="semantic",
        space_id=42,
        embedding=HalfVector([0.5, 0.25]),
        source_hash="h",
        source_text="doc",
        created_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    session = _Session([_Result([orm_row])])
    with patch(f"{REPO}.read_scope", _scope(session)):
        got = await ResourceEmbeddingsRepository().get(7, "semantic", 42)

    assert got is not None
    assert got["embedding"] == [0.5, 0.25]
    assert all(type(x) is float for x in got["embedding"])
    assert got["source_hash"] == "h"
    assert got["resource_id"] == 7 and got["space_id"] == 42


async def test_get_missing_row_is_none():
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    session = _Session([_Result([])])
    with patch(f"{REPO}.read_scope", _scope(session)):
        assert await ResourceEmbeddingsRepository().get(7, "semantic", 42) is None


# ── coverage / pending_for_user ──────────────────────────────────────────


async def test_coverage_returns_covered_and_total():
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    session = _Session([_Result(scalar=10), _Result(scalar=3)])
    with patch(f"{REPO}.read_scope", _scope(session)):
        got = await ResourceEmbeddingsRepository().coverage(
            user_id=USER, space_id=42, layer="semantic"
        )
    assert got == (3, 10)
    covered_sql = _sql(session.calls[1][0])
    assert "resource_embeddings" in covered_sql
    assert "resource_embeddings.space_id" in covered_sql
    assert "resource_embeddings.layer" in covered_sql


async def test_pending_for_user_refuses_falsy_user_id():
    """A falsy owner would render as ``creator_id IS NULL`` and select the
    orphan rows."""
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    with patch(f"{REPO}.read_scope", lambda: _Unreachable()):
        got = await ResourceEmbeddingsRepository().pending_for_user(
            user_id="", space_id=42, layer="semantic", limit=10, doc_version="v9"
        )
    assert got == ([], 0)


async def test_pending_for_user_builds_rows_with_their_reason():
    from app.repositories.resource_embeddings_repository import (
        BackfillRow,
        ResourceEmbeddingsRepository,
    )

    rows = [
        (1, 11, "p1", "T1", None, 1, "missing"),
        (2, 22, "p2", None, "D2", None, "stale_version"),
        (3, 33, "p3", "T3", "D3", None, "stale_source"),
    ]
    session = _Session([_Result(scalar=5), _Result(rows)])
    with patch(f"{REPO}.read_scope", _scope(session)):
        got, total = await ResourceEmbeddingsRepository().pending_for_user(
            user_id=USER, space_id=42, layer="semantic", limit=3, doc_version="v9"
        )

    assert total == 5
    assert got == [
        BackfillRow(1, 11, "p1", "T1", "", True, "missing"),
        BackfillRow(2, 22, "p2", "", "D2", False, "stale_version"),
        BackfillRow(3, 33, "p3", "T3", "D3", False, "stale_source"),
    ]


async def test_pending_for_user_selects_missing_and_stale_missing_first():
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    session = _Session([_Result(scalar=0), _Result([])])
    with patch(f"{REPO}.read_scope", _scope(session)):
        await ResourceEmbeddingsRepository().pending_for_user(
            user_id=USER,
            space_id=42,
            layer="semantic",
            limit=2,
            doc_version="semantic_v2",
        )
    stmt = session.calls[1][0]
    sql = _sql(stmt)
    assert "LEFT OUTER JOIN public.resource_embeddings" in sql
    assert "resource_embeddings.resource_id IS NULL" in sql
    # (b) stale version: the prefix is a LIKE pattern, so the ``_`` in
    # "semantic_v2" must be escaped or it matches any character.
    assert "resource_embeddings.source_hash NOT LIKE" in sql
    params = stmt.compile(dialect=postgresql.dialect()).params
    assert "semantic\\_v2:%" in params.values()
    # (b) stale source: a summary / transcript newer than the vector.
    assert "public.resource_summaries" in sql
    assert "public.resource_transcripts" in sql
    assert sql.count("> public.resource_embeddings.updated_at") == 2
    order = sql[sql.index("ORDER BY") :]
    assert (
        order.index("resource_embeddings.resource_id IS NOT NULL")
        < order.index("public.resource_analysis.resource_id IS NULL")
        < order.index("public.resources.id DESC")
    )


async def test_stale_count_counts_only_rows_that_have_a_vector():
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    session = _Session([_Result(scalar=4)])
    with patch(f"{REPO}.read_scope", _scope(session)):
        got = await ResourceEmbeddingsRepository().stale_count(
            user_id=USER, space_id=42, layer="semantic", doc_version="v9"
        )
    assert got == 4
    sql = _sql(session.calls[0][0])
    assert "JOIN public.resource_embeddings" in sql
    assert "LEFT OUTER JOIN public.resource_embeddings" not in sql
    assert "source_hash NOT LIKE" in sql


async def test_stale_count_refuses_falsy_user_id():
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    with patch(f"{REPO}.read_scope", lambda: _Unreachable()):
        got = await ResourceEmbeddingsRepository().stale_count(
            user_id="", space_id=42, layer="semantic", doc_version="v9"
        )
    assert got == 0


async def test_rewrite_hash_changes_only_the_hash_and_updated_at():
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    session = _Session()
    with patch(f"{REPO}.write_scope", _scope(session)):
        await ResourceEmbeddingsRepository().rewrite_hash(
            7, "semantic", 42, old_hash="abc", new_hash="v:abc"
        )
    stmt = session.calls[0][0]
    sql = _sql(stmt)
    assert sql.startswith(
        "UPDATE public.resource_embeddings SET source_hash=%(source_hash)s, "
        "updated_at=now()"
    )
    set_clause = sql.split(" SET ", 1)[1].split("WHERE")[0]
    assert "embedding" not in set_clause, "a relabel must not rewrite the vector"
    # Compare-and-set: a concurrent re-embed wins over the relabel.
    assert "resource_embeddings.source_hash = " in sql.split("WHERE")[1]
    params = stmt.compile(dialect=postgresql.dialect()).params
    assert params["source_hash"] == "v:abc" and "abc" in params.values()


async def test_all_three_listings_count_the_same_population():
    """coverage / stale_count / pending_for_user must agree on "the user's
    resources": a resource without a parsed_media row is in none of them."""
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    repo = ResourceEmbeddingsRepository()
    session = _Session([_Result(scalar=0)] * 5 + [_Result([])])
    with patch(f"{REPO}.read_scope", _scope(session)):
        await repo.coverage(user_id=USER, space_id=42, layer="semantic")
        await repo.stale_count(
            user_id=USER, space_id=42, layer="semantic", doc_version="v9"
        )
        await repo.pending_for_user(
            user_id=USER, space_id=42, layer="semantic", limit=1, doc_version="v9"
        )
    sqls = [_sql(c[0]) for c in session.calls]
    assert len(sqls) == 5
    for sql in sqls:
        assert "JOIN public.parsed_media ON public.parsed_media.id = " in sql
        assert "public.resources.media_id IS NOT NULL" in sql


async def test_touch_moves_updated_at_only():
    from app.repositories.resource_embeddings_repository import (
        ResourceEmbeddingsRepository,
    )

    session = _Session()
    with patch(f"{REPO}.write_scope", _scope(session)):
        await ResourceEmbeddingsRepository().touch(7, "semantic", 42)
    sql = _sql(session.calls[0][0])
    assert sql.startswith("UPDATE public.resource_embeddings SET updated_at=now()")
    for col in ("resource_id", "layer", "space_id"):
        assert f"resource_embeddings.{col} =" in sql


# ── EmbeddingSpaceRepository ─────────────────────────────────────────────


async def test_get_or_create_space_is_idempotent_on_model_dims():
    from app.repositories.embedding_space_repository import EmbeddingSpaceRepository

    orm_row = SimpleNamespace(
        id=99,
        actual_model="doubao-embedding-vision-251215",
        protocol="ark-multimodal",
        dims=2048,
        modalities=["text", "image"],
        instruction_version="en_keyword_v1",
        created_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
    )
    session = _Session([_Result(), _Result([orm_row])])
    spec = SpaceSpec(
        actual_model="doubao-embedding-vision-251215",
        dims=2048,
        protocol="ark-multimodal",
        modalities=("text", "image"),
    )
    with patch(f"{SPACE_REPO}.write_scope", _scope(session)):
        got = await EmbeddingSpaceRepository().get_or_create(spec)

    insert_sql = _sql(session.calls[0][0])
    assert "INSERT INTO public.embedding_spaces" in insert_sql
    assert "ON CONFLICT ON CONSTRAINT embedding_spaces_model_dims_key DO NOTHING" in (
        insert_sql
    )
    assert got == {
        "id": 99,
        "actual_model": "doubao-embedding-vision-251215",
        "protocol": "ark-multimodal",
        "dims": 2048,
        "modalities": ["text", "image"],
        "instruction_version": "en_keyword_v1",
        "created_at": "2026-09-22T00:00:00+00:00",
    }


async def test_get_or_create_space_missing_table_is_store_missing():
    from app.repositories.embedding_space_repository import EmbeddingSpaceRepository
    from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing

    session = _Session(raises=_pg_error("42P01"))
    spec = SpaceSpec(actual_model="m", dims=2048, protocol="p", modalities=("text",))
    with patch(f"{SPACE_REPO}.write_scope", _scope(session)):
        with pytest.raises(EmbeddingStoreMissing):
            await EmbeddingSpaceRepository().get_or_create(spec)


async def test_get_space_by_id():
    from app.repositories.embedding_space_repository import EmbeddingSpaceRepository

    session = _Session([_Result([])])
    with patch(f"{SPACE_REPO}.read_scope", _scope(session)):
        assert await EmbeddingSpaceRepository().get(1) is None


def test_singleton_factories():
    from app.repositories.embedding_space_repository import (
        get_embedding_space_repository,
    )
    from app.repositories.resource_embeddings_repository import (
        get_resource_embeddings_repository,
    )

    assert get_embedding_space_repository() is get_embedding_space_repository()
    assert get_resource_embeddings_repository() is get_resource_embeddings_repository()
