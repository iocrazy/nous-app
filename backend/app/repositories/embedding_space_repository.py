"""Data access for ``embedding_spaces`` (migration 499).

A space is (actual provider model id, width). Rows are created on first use
from the resolved embedder's :class:`~app.core.embedding_space.SpaceSpec`,
never typed by hand; ``get_or_create`` is idempotent on the unique key, so
two workers resolving the same embedder at once land on one row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import ProgrammingError

from app.core.embedding_space import SpaceSpec
from app.db.session import read_scope, write_scope
from app.models import EmbeddingSpaces
from app.repositories.resource_embeddings_repository import (
    EmbeddingStoreMissing,
    is_store_missing,
)

_MISSING = (
    "embedding_spaces does not exist in this database (migration 499 not applied)"
)


def _space_dict(row: Any) -> dict:
    created = row.created_at
    return {
        "id": int(row.id),
        "actual_model": row.actual_model,
        "protocol": row.protocol,
        "dims": int(row.dims),
        "modalities": list(row.modalities or []),
        "instruction_version": row.instruction_version,
        "created_at": created.isoformat() if isinstance(created, datetime) else created,
    }


class EmbeddingSpaceRepository:
    async def get_or_create(self, spec: SpaceSpec) -> dict:
        """Return the space row for ``(spec.actual_model, spec.dims)``,
        inserting it first if absent. An existing row is returned as stored:
        the unique key is the identity, the other fields describe it."""
        stmt = (
            pg_insert(EmbeddingSpaces)
            .values(
                actual_model=spec.actual_model,
                dims=spec.dims,
                protocol=spec.protocol,
                modalities=list(spec.modalities),
                instruction_version=spec.instruction_version,
            )
            .on_conflict_do_nothing(constraint="embedding_spaces_model_dims_key")
        )
        lookup = select(EmbeddingSpaces).where(
            EmbeddingSpaces.actual_model == spec.actual_model,
            EmbeddingSpaces.dims == spec.dims,
        )
        try:
            async with write_scope() as session:
                await session.execute(stmt)
                row = (await session.execute(lookup)).scalars().first()
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise EmbeddingStoreMissing(_MISSING) from exc
            raise
        if row is None:
            # ON CONFLICT DO NOTHING + SELECT in one transaction always sees
            # a row; reaching here means the key predicate and the unique
            # constraint disagree — a defect, not "no space".
            raise RuntimeError(
                f"embedding space {spec.actual_model!r}/{spec.dims} not found "
                "right after insert-or-ignore"
            )
        return _space_dict(row)

    async def get(self, space_id: int) -> dict | None:
        try:
            async with read_scope() as session:
                row = (
                    (
                        await session.execute(
                            select(EmbeddingSpaces).where(
                                EmbeddingSpaces.id == space_id
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
        except ProgrammingError as exc:
            if is_store_missing(exc):
                raise EmbeddingStoreMissing(_MISSING) from exc
            raise
        return _space_dict(row) if row is not None else None


_repository: EmbeddingSpaceRepository | None = None


def get_embedding_space_repository() -> EmbeddingSpaceRepository:
    global _repository
    if _repository is None:
        _repository = EmbeddingSpaceRepository()
    return _repository
