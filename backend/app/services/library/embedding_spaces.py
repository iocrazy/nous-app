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

from loguru import logger

from app.repositories.admin.system_settings_repository import (
    get_system_settings_repository,
)
from app.services.ai.providers.embedding_config import (
    EmbeddingConfig,
    platform_embedding_config,
)
from app.services.ai.providers.embedding_service import EmbeddingService
from app.services.library.semantic_store import forget_space_id

#: The governance key that names the active embedder (a catalog ``name``).
EMBEDDING_MODEL_SETTING = "ai_module.embedding.model"
#: The legacy key ``resolve_embedding_config`` falls back to when the
#: governance key is blank.
LEGACY_EMBEDDING_MODEL_SETTING = "graph_embedder_model"

#: What the Add Space probe embeds: any short text; only the width matters.
PROBE_TEXT = "probe"

#: ``nous_models.type`` of an embedding model.
_EMBEDDING_TYPE = "embedding"

SPACE_CATALOG_CODES = (
    "catalog_model_not_found",
    "catalog_model_disabled",
    "not_an_embedding_model",
    "space_catalog_row_missing",
    # A personal (BYOK) catalog row: its key belongs to one user and must
    # never embed a platform space everyone searches.
    "byok_row_not_allowed",
)


class SpaceCatalogError(Exception):
    """A catalog pick (or a space's catalog row) cannot serve as an embedder.
    ``code`` is one of :data:`SPACE_CATALOG_CODES`."""

    def __init__(self, code: str, message: str) -> None:
        if code not in SPACE_CATALOG_CODES:
            raise ValueError(f"unknown space catalog code {code!r}")
        self.code = code
        super().__init__(message)


class ActiveSpaceUnknown(RuntimeError):
    """The active embedder could not be determined (settings unreadable, or
    the configured catalog row cannot be resolved). Anything destructive must
    refuse on it — "could not tell" is never "nothing is active"."""


def _repo():
    from app.repositories.nous_model_repository import get_nous_model_repository

    return get_nous_model_repository()


def _check_usable(row: Optional[Dict[str, Any]], name: str, missing: str) -> dict:
    if not row:
        raise SpaceCatalogError(missing, f"no catalog model {name!r}")
    if row.get("owner_user_id"):
        raise SpaceCatalogError(
            "byok_row_not_allowed", f"catalog model {name!r} is a personal key"
        )
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


async def platform_embedding_models() -> Dict[str, Any]:
    """What Add Space may pick: ``{"models": [public rows], "engine": …}``.

    The platform provider view's SYSTEM computation (``platform_rows`` with
    no user): governance, live engine state, ``fail`` rows dropped, and
    platform-wide rows only — a space is shared, so neither an admin's own
    owner-scoped rows nor their personal blacklist apply. Each row's
    ``last_test_status`` is the live status (``ok`` / ``idle`` /
    ``not_probed``). A failed read degrades to no models (logged)."""
    from app.services.ai.platform_provider import platform_rows_and_engine

    rows, engine = await platform_rows_and_engine(
        None, type="embedding", purpose="system"
    )
    return {
        "models": [r.model.public_row() for r in rows],
        "engine": engine.as_dict() if engine else None,
    }


async def catalog_name_for(actual_model: str) -> Optional[str]:
    """Catalog ``name`` of the platform row serving ``actual_model``, or None
    (also on a read failure: this only labels a card)."""
    try:
        row = await _repo().get_platform_embedding_by_actual_model(actual_model)
    except Exception as e:  # noqa: BLE001 — a label, not a decision
        logger.error(f"[embedding_spaces] catalog lookup for {actual_model!r}: {e}")
        return None
    return row.get("name") if row else None


async def catalog_row_for_space(space: Dict[str, Any]) -> Dict[str, Any]:
    """The usable catalog row a space maps to (by ``actual_model``). Raises
    :class:`SpaceCatalogError` (``space_catalog_row_missing`` when the row
    was deleted from the catalog)."""
    actual = space["actual_model"]
    row = await _repo().get_platform_embedding_by_actual_model(actual)
    return _check_usable(row, actual, "space_catalog_row_missing")


async def service_for_space(space: Dict[str, Any]) -> EmbeddingService:
    """An embedder that writes into ``space`` — built from that space's own
    catalog row, never from the active governance setting."""
    row = await catalog_row_for_space(space)
    return EmbeddingService(cfg=await config_for_catalog_model(row["name"]))


async def _actual_model_of(name: str) -> str:
    """What ``resolve_embedding_config`` would embed with for ``name``: the
    catalog row's ``actual_model``, or the string itself (a manual model id).
    A disabled catalog row raises (the resolver would silently fall through
    to another branch — too many paths to mirror for a destructive check)."""
    from app.services.ai.providers.ai_provider_helpers import resolve_platform_model

    platform = await resolve_platform_model(name)
    return platform[2] if platform is not None else name


async def active_actual_model() -> Optional[str]:
    """``actual_model`` of the ACTIVE space, read from the settings directly
    (not through ``EmbeddingService.space_spec``, which folds every failure
    into None). None ONLY when neither the governance key nor the legacy
    ``graph_embedder_model`` is set; every failure raises
    :class:`ActiveSpaceUnknown`."""
    repo = get_system_settings_repository()
    try:
        for key in (EMBEDDING_MODEL_SETTING, LEGACY_EMBEDDING_MODEL_SETTING):
            name = str(await repo.get_value(key) or "").strip()
            if name:
                return await _actual_model_of(name)
    except Exception as e:  # noqa: BLE001 — every failure is "unknown"
        raise ActiveSpaceUnknown(f"active embedding model unknown: {e}") from e
    return None


__all__ = [
    "ActiveSpaceUnknown",
    "EMBEDDING_MODEL_SETTING",
    "active_actual_model",
    "platform_embedding_models",
    "PROBE_TEXT",
    "SPACE_CATALOG_CODES",
    "SpaceCatalogError",
    "catalog_name_for",
    "catalog_row_for_space",
    "config_for_catalog_model",
    "forget_space_id",
    "service_for_space",
]
