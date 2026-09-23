"""Vector reads of the semantic layer, for the three search paths.

hybrid (``SearchService._vector_hits``), ``semantic_search`` and
``find_similar_media`` all read here. Since migration 494 the vectors live in
``resource_embeddings`` (halfvec + HNSW, one space per embedder); the legacy
``resource_analysis.content_embedding`` column is a READ-ONLY fallback that
goes away next release. The two reads fall back differently:

* :meth:`SemanticStore.nearest` reads the legacy column only when the new
  store cannot be asked at all — ``EmbeddingStoreMissing`` (code deployed
  before migration 494) or an embedder that cannot name its space. A
  half-filled ``resource_embeddings`` is NOT topped up from the old column:
  rows not yet backfilled simply do not match.
* :meth:`SemanticStore.similar` falls back per source resource: when that
  resource has no row in the current space (or the store is missing), its
  legacy vector is used against the legacy column.

When neither store is callable, :class:`EmbeddingSearchUnavailable`
propagates: the caller must say "the vector store is missing", never "no
match".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger

from app.core.embedding_space import SEMANTIC_LAYER, SpaceSpec
from app.repositories.resource_embeddings_repository import EmbeddingStoreMissing

# Space ids by spec, per process. A space row is immutable identity (its
# unique key IS the spec), so once resolved it never changes; without this
# every hybrid query would pay an INSERT … ON CONFLICT round-trip.
_SPACE_ID_CACHE: Dict[SpaceSpec, int] = {}


def parse_embedding(value: Any) -> Optional[List[float]]:
    """A pgvector value as floats: a list passes through, the ``[a,b]`` text
    form is parsed; anything else is None."""
    if isinstance(value, list):
        return value
    try:
        return [float(x.strip()) for x in str(value).strip("[]").split(",")]
    except (TypeError, ValueError) as e:
        logger.error(f"Error parsing embedding: {e}")
        return None


@dataclass(frozen=True)
class SemanticStore:
    """The four collaborators a vector read needs. Stateless apart from the
    module-level space cache."""

    embedding_service: Any
    analysis_repo: Any
    space_repo: Any
    embeddings_repo: Any

    async def current_space_id(self) -> Optional[int]:
        """Id of the embedder's space, or None when the embedder cannot name
        one. Raises :class:`EmbeddingStoreMissing` before migration 494."""
        spec = await self.embedding_service.space_spec()
        if spec is None:
            return None
        space_id = _SPACE_ID_CACHE.get(spec)
        if space_id is None:
            space = await self.space_repo.get_or_create(spec)
            space_id = _SPACE_ID_CACHE[spec] = int(space["id"])
        return space_id

    async def nearest(
        self, vec: List[float], *, user_id: str, limit: int, threshold: float
    ) -> List[Dict[str, Any]]:
        """Nearest neighbours of ``vec`` in the semantic layer of the
        embedder's space; the legacy RPC answers before migration 494."""
        try:
            space_id = await self.current_space_id()
            if space_id is not None:
                return await self.embeddings_repo.search(
                    embedding=vec,
                    space_id=space_id,
                    layer=SEMANTIC_LAYER,
                    user_id=user_id,
                    limit=limit,
                    threshold=threshold,
                )
        except EmbeddingStoreMissing as e:
            logger.warning(
                "[search] resource_embeddings unavailable (migration 494 not "
                f"applied), reading the legacy column: {e}"
            )
        return await self.analysis_repo.search_by_embedding(
            embedding=vec,
            user_id=user_id,
            limit=limit,
            threshold=threshold,
            embedding_model=self.embedding_service.model or None,
        )

    async def similar(
        self, resource_id: int, *, user_id: str, limit: int, threshold: float
    ) -> Optional[List[Dict[str, Any]]]:
        """Neighbours of one resource's own vector (``limit + 1`` rows: the
        caller drops the self-match). None when the source has no vector in
        either store — "nothing to compare with", not "no neighbours"."""
        try:
            rows = await self._similar_from_store(
                resource_id, user_id, limit, threshold
            )
        except EmbeddingStoreMissing as e:
            logger.warning(
                "[similar] resource_embeddings unavailable (migration 494 not "
                f"applied), reading the legacy column: {e}"
            )
            rows = None
        if rows is not None:
            return rows
        return await self._similar_from_legacy(resource_id, user_id, limit, threshold)

    async def _similar_from_store(
        self, resource_id: int, user_id: str, limit: int, threshold: float
    ) -> Optional[List[Dict[str, Any]]]:
        space_id = await self.current_space_id()
        if space_id is None:
            return None
        stored = await self.embeddings_repo.get(
            int(resource_id), SEMANTIC_LAYER, space_id
        )
        if not stored or not stored.get("embedding"):
            return None
        return await self.embeddings_repo.search(
            embedding=stored["embedding"],
            space_id=space_id,
            layer=SEMANTIC_LAYER,
            user_id=user_id,
            limit=limit + 1,
            threshold=threshold,
        )

    async def _similar_from_legacy(
        self, resource_id: int, user_id: str, limit: int, threshold: float
    ) -> Optional[List[Dict[str, Any]]]:
        analysis = await self.analysis_repo.get_analysis(resource_id)
        if not analysis or not analysis.get("content_embedding"):
            return None
        embedding = parse_embedding(analysis["content_embedding"])
        if not embedding:
            return None
        # Within the source vector's space (None = legacy, compatible).
        return await self.analysis_repo.search_by_embedding(
            embedding=embedding,
            user_id=user_id,
            limit=limit + 1,
            threshold=threshold,
            embedding_model=analysis.get("embedding_model"),
        )
