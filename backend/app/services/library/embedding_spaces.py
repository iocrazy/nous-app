"""Embedding space switching: a catalog row <-> a space <-> an embedder.

The ACTIVE space is whatever the admin governance setting
``ai_module.embedding.model`` resolves to (``resolve_embedding_config``);
nothing else marks a space active. A *candidate* space is any other row of
``embedding_spaces``: it is filled by its own embedder
(:func:`service_for_space`) and becomes active when its catalog row's
``name`` is written to that setting (``POST /search/vectors/spaces/{id}/
activate``).

Space identity is ``(actual_model, dims)`` and dims is always
:data:`~app.core.embedding_space.EMBEDDING_DIM`, so a space maps back to its
catalog row by ``actual_model`` equality — a catalog rename does not orphan
a space, deleting the catalog row does (``space_catalog_row_missing``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.ai.providers.embedding_config import (
    EmbeddingConfig,
    platform_embedding_config,
)
from app.services.ai.providers.embedding_service import EmbeddingService
from app.services.library.semantic_store import forget_space_id

#: The governance key that names the active embedder (a catalog ``name``).
EMBEDDING_MODEL_SETTING = "ai_module.embedding.model"

#: What the Add Space probe embeds: any short text; only the width matters.
PROBE_TEXT = "probe"

#: ``nous_models.type`` of an embedding model.
_EMBEDDING_TYPE = "embedding"

SPACE_CATALOG_CODES = (
    "catalog_model_not_found",
    "catalog_model_disabled",
    "not_an_embedding_model",
    "space_catalog_row_missing",
)


class SpaceCatalogError(Exception):
    """A catalog pick (or a space's catalog row) cannot serve as an embedder.
    ``code`` is one of :data:`SPACE_CATALOG_CODES`."""

    def __init__(self, code: str, message: str) -> None:
        if code not in SPACE_CATALOG_CODES:
            raise ValueError(f"unknown space catalog code {code!r}")
        self.code = code
        super().__init__(message)


def _repo():
    from app.repositories.nous_model_repository import get_nous_model_repository

    return get_nous_model_repository()


def _check_usable(row: Optional[Dict[str, Any]], name: str, missing: str) -> dict:
    if not row:
        raise SpaceCatalogError(missing, f"no catalog model {name!r}")
    if (row.get("type") or "") != _EMBEDDING_TYPE:
        raise SpaceCatalogError(
            "not_an_embedding_model", f"catalog model {name!r} is not an embedder"
        )
    if not row.get("is_enabled"):
        raise SpaceCatalogError(
            "catalog_model_disabled", f"catalog model {name!r} is disabled"
        )
    return row


async def config_for_catalog_model(name: str) -> EmbeddingConfig:
    """The embedder config of catalog row ``name`` — same resolution
    (``resolve_platform_model``) and shape as the active embedder's
    platform branch. Raises :class:`SpaceCatalogError`."""
    from app.services.ai.providers.ai_provider_helpers import resolve_platform_model

    _check_usable(await _repo().get_by_name(name), name, "catalog_model_not_found")
    try:
        platform = await resolve_platform_model(name)
    except RuntimeError as e:  # found-but-disabled, raced the check above
        raise SpaceCatalogError("catalog_model_disabled", str(e)) from e
    if platform is None:
        raise SpaceCatalogError("catalog_model_not_found", f"no catalog model {name!r}")
    _provider, provider_cfg, actual_model = platform
    return platform_embedding_config(provider_cfg, actual_model)


async def catalog_name_for(actual_model: str) -> Optional[str]:
    """Catalog ``name`` of the row serving ``actual_model``, or None."""
    row = await _repo().get_by_actual_model(actual_model)
    return row.get("name") if row else None


async def catalog_row_for_space(space: Dict[str, Any]) -> Dict[str, Any]:
    """The usable catalog row a space maps to (by ``actual_model``). Raises
    :class:`SpaceCatalogError` (``space_catalog_row_missing`` when the row
    was deleted from the catalog)."""
    actual = space["actual_model"]
    row = await _repo().get_by_actual_model(actual)
    return _check_usable(row, actual, "space_catalog_row_missing")


async def service_for_space(space: Dict[str, Any]) -> EmbeddingService:
    """An embedder that writes into ``space`` — built from that space's own
    catalog row, never from the active governance setting."""
    row = await catalog_row_for_space(space)
    return EmbeddingService(cfg=await config_for_catalog_model(row["name"]))


__all__ = [
    "EMBEDDING_MODEL_SETTING",
    "PROBE_TEXT",
    "SPACE_CATALOG_CODES",
    "SpaceCatalogError",
    "catalog_name_for",
    "catalog_row_for_space",
    "config_for_catalog_model",
    "forget_space_id",
    "service_for_space",
]
