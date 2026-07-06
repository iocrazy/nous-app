"""DB-catalog image-provider resolution.

House rule (config env→DB): image-provider credentials live in the
``mediahub_models`` catalog, NOT env. The in-process ``provider_registry`` ships
EMPTY for images (no in-proc image providers today), so image generation
resolves its provider dynamically from the DB here.

``resolve_image_provider`` picks an enabled ``type='image'`` catalog row —
by ``name``/``actual_model`` match when a name is supplied, else the first
enabled row by ``sort_order`` — decrypts its ``api_key`` (the repository's
full-row read reveals it) and builds the concrete provider. Only Ark
(``actual_provider`` in {'doubao', 'ark'}) is wired today; any other provider
raises a clear error rather than returning a broken shell.
"""

from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger

from app.services.media.parsers.video_providers.ark_image import ArkImageProvider
from app.services.media.parsers.video_providers.base import BaseImageProvider

_ARK_PROVIDERS = {"doubao", "ark"}


async def resolve_image_provider(
    name: Optional[str] = None,
) -> Tuple[BaseImageProvider, str]:
    """Resolve an image provider from the ``mediahub_models`` catalog.

    Args:
        name: Optional catalog ``name`` (or ``actual_model``) to prefer. When it
            doesn't match any enabled image row — including registry provider
            names like ``"openai"`` that never live in the catalog — the first
            enabled image row wins.

    Returns:
        ``(provider, actual_model)`` — the concrete provider plus the catalog
        row's raw upstream model id (the model the caller should generate with
        when it didn't pass an explicit non-default one).

    Raises:
        RuntimeError: no enabled image model is configured, or the resolved
            row's ``actual_provider`` has no wired provider implementation.
    """
    from app.repositories.mediahub_model_repository import (
        get_mediahub_model_repository,
    )

    repo = get_mediahub_model_repository()
    # list_all reveals api_key on every full row and is ordered by sort_order;
    # list_enabled returns public columns only (no api_key/base_url), so it
    # can't build a provider. The catalog is small, so scanning it is cheap.
    rows = await repo.list_all()
    image_rows = [r for r in rows if r.get("type") == "image" and r.get("is_enabled")]
    if not image_rows:
        raise RuntimeError("no image model configured in mediahub_models catalog")

    row = None
    if name:
        row = next(
            (
                r
                for r in image_rows
                if r.get("name") == name or r.get("actual_model") == name
            ),
            None,
        )
    if row is None:
        row = image_rows[0]

    actual_provider = (row.get("actual_provider") or "").lower()
    actual_model = row.get("actual_model") or ""
    if actual_provider in _ARK_PROVIDERS:
        provider = ArkImageProvider(
            api_key=row.get("api_key") or "",
            base_url=row.get("base_url") or "",
            default_model=actual_model,
        )
        logger.info(
            "Resolved image provider from catalog: {} (model={})",
            actual_provider,
            actual_model,
        )
        return provider, actual_model

    raise RuntimeError(
        f"No image provider implementation for actual_provider="
        f"{actual_provider!r} (catalog row name={row.get('name')!r})"
    )
