"""Backfill the semantic layer of ``resource_embeddings`` (migration 497).

Why this exists: ``analyze_l1`` ran for months with the embedder unconfigured
and swallowed the ``None``, so most downloads have no vector. Candidates come
from :meth:`ResourceEmbeddingsRepository.missing_for_user` (the caller's
resources with no row in the current space + layer).

Every candidate is embedded IN PLACE: one embedding call, no VLM. Before PR 2
the document was made of the VLM analysis fields, so a resource without an
analysis row had to be sent through the whole ``analyze_l1`` workflow first
(a Task Center row and a VLM bill each). The semantic document
(``embedding_document``) now uses whatever the resource has — title,
description, tags, summary, transcript excerpt, and the analysis when there
is one — so that dispatch branch is gone.
"""

from __future__ import annotations

from typing import Any, List, Optional, Protocol, Tuple

from loguru import logger

from app.core.embedding_space import SEMANTIC_LAYER
from app.repositories.resource_embeddings_repository import (
    BackfillRow,
    EmbeddingStoreMissing,
)
from app.services.library.embedding_document import (
    compose_semantic_document,
    load_semantic_inputs,
)


class _Embedder(Protocol):
    async def try_embed(
        self, text: str
    ) -> Tuple[Optional[List[float]], Optional[str]]: ...


class _EmbeddingsRepo(Protocol):
    async def get(self, resource_id: int, layer: str, space_id: int) -> dict | None: ...

    async def upsert(
        self,
        *,
        resource_id: int,
        layer: str,
        space_id: int,
        embedding: list[float],
        source_hash: str,
        source_text: str | None,
    ) -> None: ...


async def embed_candidate(
    row: BackfillRow,
    *,
    embedder: _Embedder,
    space_id: int,
    repo: _EmbeddingsRepo,
    analysis_repo: Any = None,
    tags_repo: Any = None,
    ai_repo: Any = None,
) -> Tuple[bool, Optional[str]]:
    """Compose the semantic document of ``row`` and write its vector into
    ``space_id``. ``embedder`` must be the embedder that space was resolved
    from.

    Returns ``(True, None)`` when the row now holds a current vector (written
    now, or already there with the same ``source_hash`` — no call, no spend),
    else ``(False, reason)``: ``"empty_text"``, ``"store_missing"`` (migration
    497 not applied), or the embedder's ``try_embed`` reason (classify it with
    ``classify_embed_reason`` before it reaches a user)."""
    inputs = await load_semantic_inputs(
        row.resource_id,
        analysis_repo=analysis_repo,
        tags_repo=tags_repo,
        ai_repo=ai_repo,
    )
    text, source_hash = compose_semantic_document(**inputs)
    if not text:
        return False, "empty_text"
    try:
        # Only reachable for rows that got a vector after missing_for_user
        # listed them (a concurrent analyze_l1, a repeated row): the listing
        # itself excludes resources that already have one, so a changed hash
        # on an existing row is not picked up here yet (spec §9).
        existing = await repo.get(row.resource_id, SEMANTIC_LAYER, space_id)
        if existing is not None and existing.get("source_hash") == source_hash:
            return True, None
        vector, reason = await embedder.try_embed(text)
        if vector is None:
            return False, reason or "provider_error: empty result"
        await repo.upsert(
            resource_id=row.resource_id,
            layer=SEMANTIC_LAYER,
            space_id=space_id,
            embedding=vector,
            source_hash=source_hash,
            source_text=text,
        )
    except EmbeddingStoreMissing as e:
        logger.error(f"backfill: vector store missing (migration 497): {e}")
        return False, "store_missing"
    return True, None
