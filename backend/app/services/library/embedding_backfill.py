"""Backfill the semantic layer of ``resource_embeddings`` (migration 499).

Why this exists: ``analyze_l1`` ran for months with the embedder unconfigured
and swallowed the ``None``, so most downloads have no vector. Candidates come
from :meth:`ResourceEmbeddingsRepository.pending_for_user`: the caller's
resources with no row in the current space + layer, or with a stale one
(written by another ``DOC_VERSION``, or older than the resource's summary /
transcript).

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

#: ``embed_candidate`` outcome for a row whose vector was current but whose
#: hash predated the ``"<version>:<sha1>"`` format: the label was rewritten,
#: no embedding call was made.
REHASHED = "rehashed"


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

    async def touch(self, resource_id: int, layer: str, space_id: int) -> None: ...

    async def rewrite_hash(
        self,
        resource_id: int,
        layer: str,
        space_id: int,
        *,
        old_hash: str,
        new_hash: str,
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
    ``(True, REHASHED)`` when the stored vector already embedded this exact
    document under a legacy bare-sha1 hash (the hash was relabelled, no
    call), else ``(False, reason)``: ``"empty_text"``, ``"store_missing"`` (migration
    499 not applied), or the embedder's ``try_embed`` reason (classify it with
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
        # The same hash means the stored vector already embeds this exact
        # document (a concurrent analyze_l1, or a stale_source row whose new
        # summary / transcript did not change the text): no call, no spend.
        existing = await repo.get(row.resource_id, SEMANTIC_LAYER, space_id)
        if existing is not None and existing.get("source_hash") == source_hash:
            if row.reason == "stale_source":
                # Move updated_at past the newer summary / transcript, or the
                # listing picks this row again on every run.
                await repo.touch(row.resource_id, SEMANTIC_LAYER, space_id)
            return True, None
        # Written before the "<version>:<sha1>" format: the bare digest of the
        # same versioned text means the vector is current, only the label is
        # old. Relabel it instead of paying to embed identical text again.
        bare_digest = source_hash.split(":", 1)[1]
        if existing is not None and existing.get("source_hash") == bare_digest:
            await repo.rewrite_hash(
                row.resource_id,
                SEMANTIC_LAYER,
                space_id,
                old_hash=bare_digest,
                new_hash=source_hash,
            )
            return True, REHASHED
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
        logger.error(f"backfill: vector store missing (migration 499): {e}")
        return False, "store_missing"
    return True, None
