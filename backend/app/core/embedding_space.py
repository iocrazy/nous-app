"""The ONE place that knows what "the current embedding space" is.

A vector only means something next to vectors from the same model. Two
things can go wrong when the admin-configured embedder changes, and both used
to be silent:

* **Different width** — pgvector rejects the vector at bind / CAST time, and
  every writer read that error as "no vector, skip". The column simply stopped
  filling. :func:`ensure_embedding_dim` now refuses it at the writer seam
  (``EmbeddingService``) with a typed :class:`EmbeddingDimensionMismatch`.
* **Same width, different model** — nothing rejects it. New vectors land next
  to old ones and ``<=>`` compares two unrelated spaces, producing confident,
  meaningless scores. Every vector column therefore has an ``embedding_model``
  sibling (migration 490), and readers compare only vectors of the same space.

Space identity = the embedder's **actual provider model id**
(``EmbeddingConfig.model``, e.g. ``doubao-embedding-vision-251215``), never
the catalog name: a catalog row can be renamed (mig 485/488 did exactly that)
without the space changing, and two catalog names can point at one model.

NULL ``embedding_model`` = a vector written before migration 490 ("legacy").
Readers treat NULL as compatible with any space, i.e. as the current one —
true today, because only one embedder has ever filled these columns.
**Before switching the embedder, stamp the legacy rows with the OLD model id**
so they stop matching the new space::

    UPDATE public.resource_analysis SET embedding_model = '<old model id>'
     WHERE content_embedding IS NOT NULL AND embedding_model IS NULL;
    -- likewise hotspots / topic_groups / user_topic_interests (.embedding)

Switching the width means changing :data:`EMBEDDING_DIM` here (the four ORM
``Vector(...)`` declarations read it) together with a column migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# Width of every pgvector column (hotspots / topic_groups /
# user_topic_interests .embedding, resource_analysis.content_embedding).
# doubao-embedding-vision; the columns are vector(2048) since mig 314/315.
EMBEDDING_DIM = 2048


class EmbeddingDimensionMismatch(ValueError):
    """The embedder returned a vector whose width is not :data:`EMBEDDING_DIM`.

    Not a transient provider failure: every later call returns the same
    width, so the caller must surface it (ERROR log, typed reason, typed
    response field) instead of treating it as "no vector this time".
    """

    def __init__(self, *, expected: int, got: int, model: str) -> None:
        self.expected = expected
        self.got = got
        self.model = model
        super().__init__(
            f"embedding model {model!r} returned {got} dimensions; the vector "
            f"columns hold {expected}"
        )


def ensure_embedding_dim(vec: Sequence[float], *, model: str) -> None:
    """Raise :class:`EmbeddingDimensionMismatch` unless ``vec`` fits the
    columns."""
    if len(vec) != EMBEDDING_DIM:
        raise EmbeddingDimensionMismatch(
            expected=EMBEDDING_DIM, got=len(vec), model=model
        )


def spaces_compatible_sql(left: str, right: str) -> str:
    """SQL predicate: two ``embedding_model`` expressions name the same space.

    NULL on either side is the legacy / current space and matches anything —
    see the module docstring for why that holds and what to do before it
    stops holding. ``left`` / ``right`` are trusted SQL fragments (column
    references or ``CAST(:param AS text)``), never user input.
    """
    return f"({left} IS NULL OR {right} IS NULL OR {left} = {right})"


SEMANTIC_LAYER = "semantic"
LAYERS: tuple[str, ...] = ("semantic", "transcript")


@dataclass(frozen=True)
class SpaceSpec:
    """Identity of an embedding space, derived from the resolved embedder
    (never typed by hand). ``(actual_model, dims)`` is the unique key."""

    actual_model: str
    dims: int
    protocol: str
    modalities: tuple[str, ...]
    instruction_version: str = "en_keyword_v1"


__all__ = [
    "EMBEDDING_DIM",
    "LAYERS",
    "SEMANTIC_LAYER",
    "SpaceSpec",
    "EmbeddingDimensionMismatch",
    "ensure_embedding_dim",
    "spaces_compatible_sql",
]
