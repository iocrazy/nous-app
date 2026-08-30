"""DB-catalog image/video-provider resolution.

House rule (config env→DB): image/video-provider credentials live in the
``mediahub_models`` catalog, NOT env. The in-process ``provider_registry`` ships
EMPTY for images, so generation resolves its provider dynamically from the DB
here, dispatching on the row's ``actual_provider``:

  - ``jimeng-cli`` → the subprocess-driven dreamina CLI (no api_key — the CLI's
    OAuth session is the credential). Preferred when multiple rows are enabled
    (user decision 2026-07-07: CLI is the primary; Ark rows stay enabled as a
    lower-priority fallback).
  - ``doubao`` / ``ark`` → the OpenAI-compatible Ark image endpoint.

Row SELECTION lives here (``_enabled_rows`` / ``_pick_row``); CONSTRUCTION of
the actual provider/adapter is delegated to the matching provider protocol
(``resolve_generation_protocol(actual_provider).build_image_provider(row)`` /
``.build_video_provider(row)`` — see ``app.services.ai.provider_protocols``).
Image callers use the ``BaseImageProvider.generate`` contract, so the CLI
provider is wrapped in ``_JimengImageAdapter`` (returns an ``ImageGenResult``
with ``image_path`` set — a local file, not a URL; the adapter class now lives
in ``provider_protocols.jimeng``, next to the protocol that builds it). Video
callers use ``JimengCliProvider.generate_video`` directly (only the CLI has a
wired video path today), so ``resolve_video_provider`` returns the provider
as-is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple

from loguru import logger

if TYPE_CHECKING:
    from app.services.media.parsers.video_providers.base import BaseImageProvider
    from app.services.media.parsers.video_providers.jimeng_cli import (
        JimengCliProvider,
    )


def _pick_row(rows: list[dict], name: Optional[str], jimeng_first: bool = True) -> dict:
    """Choose a catalog row: an explicit name match wins; else the preferred
    jimeng-cli row; else the first row (already ordered by sort_order)."""
    if name:
        match = next(
            (r for r in rows if r.get("name") == name or r.get("actual_model") == name),
            None,
        )
        if match is not None:
            return match
    if jimeng_first:
        from app.services.ai.provider_protocols import resolve_generation_protocol

        jimeng = next(
            (
                r
                for r in rows
                if (
                    protocol := resolve_generation_protocol(
                        r.get("actual_provider") or ""
                    )
                )
                is not None
                and protocol.generation_family == "jimeng-cli"
            ),
            None,
        )
        if jimeng is not None:
            return jimeng
    return rows[0]


async def _enabled_rows(media_type: str) -> list[dict]:
    from app.repositories.mediahub_model_repository import (
        get_mediahub_model_repository,
    )

    repo = get_mediahub_model_repository()
    # list_all reveals api_key on every full row and is ordered by sort_order;
    # list_enabled returns public columns only (no api_key/base_url).
    rows = await repo.list_all()
    return [r for r in rows if r.get("type") == media_type and r.get("is_enabled")]


def _visible_to(row: dict, user_id: Optional[str]) -> bool:
    """Owner scoping (migration 431): a NULL owner is platform-wide; a set
    owner admits only that user. No user in scope → fail-closed."""
    owner = row.get("owner_user_id")
    if not owner:
        return True
    return user_id is not None and str(owner) == str(user_id)


def _explicit_match(rows: list[dict], name: str) -> Optional[dict]:
    return next(
        (r for r in rows if r.get("name") == name or r.get("actual_model") == name),
        None,
    )


def _visible_rows(
    rows: list[dict], name: Optional[str], user_id: Optional[str], kind: str
) -> list[dict]:
    """Apply owner scoping; an explicitly-requested private row must RAISE for
    a non-owner, never silently fall back to some other public row."""
    visible = [r for r in rows if _visible_to(r, user_id)]
    if (
        name
        and _explicit_match(visible, name) is None
        and _explicit_match(rows, name) is not None
    ):
        raise RuntimeError(
            f"{kind} model {name!r} is private to another user "
            "(mediahub_models.owner_user_id)"
        )
    return visible


def _stamp_provider_key(provider: object, actual_provider: str) -> None:
    """Record which catalog row's ``actual_provider`` built this provider.

    A built provider does NOT otherwise remember its own catalog key — the
    protocol constructs a bare adapter (``ArkImageProvider``,
    ``_CodexImageAdapter``, ...) and drops the row on the floor. Callers that
    need the provider's declared capabilities (``canvas_generation``'s
    ``_capabilities_for``) would then have nothing to resolve the protocol
    back from, silently get ``ProviderCapabilities.none()``, and drop every
    knob the user asked for. This function is the choke point: the one place
    that both builds the provider and still holds the row.
    """
    provider.provider_key = actual_provider  # type: ignore[attr-defined]


async def resolve_image_provider(
    name: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
) -> Tuple[BaseImageProvider, str]:
    """Resolve an image provider from the ``mediahub_models`` catalog.

    Returns ``(provider, actual_model)``. Dispatches on ``actual_provider``;
    when several image rows are enabled and no explicit ``name`` matches, the
    jimeng-cli row is preferred (CLI is the primary generator). Owner-scoped
    rows resolve only for their owner (``user_id``); call paths that don't
    thread a user fail closed.

    Raises:
        RuntimeError: no enabled image model, the requested row is private to
            another user, or the resolved row's ``actual_provider`` has no
            wired implementation.
    """
    image_rows = _visible_rows(await _enabled_rows("image"), name, user_id, "image")
    if not image_rows:
        raise RuntimeError("no image model configured in mediahub_models catalog")

    row = _pick_row(image_rows, name)
    actual_provider = (row.get("actual_provider") or "").lower()

    from app.services.ai.provider_protocols import resolve_generation_protocol

    protocol = resolve_generation_protocol(actual_provider)
    if protocol is None:
        raise RuntimeError(
            f"No image provider implementation for actual_provider="
            f"{actual_provider!r} (catalog row name={row.get('name')!r})"
        )
    provider, actual_model = protocol.build_image_provider(row)
    _stamp_provider_key(provider, actual_provider)
    logger.info(
        "Resolved image provider from catalog: {} (model={})",
        actual_provider,
        actual_model,
    )
    return provider, actual_model


async def resolve_video_provider(
    name: Optional[str] = None,
    *,
    user_id: Optional[str] = None,
) -> Tuple[JimengCliProvider, str]:
    """Resolve a video provider from the ``mediahub_models`` catalog.

    Returns ``(provider, actual_model)``. Only the jimeng-cli CLI has a wired
    video path today; callers use ``provider.generate_video(...)`` directly.
    Owner-scoped rows resolve only for their owner (see
    ``resolve_image_provider``).

    Raises:
        RuntimeError: no enabled video model, the requested row is private to
            another user, or the resolved row's ``actual_provider`` has no
            wired video implementation.
    """
    video_rows = _visible_rows(await _enabled_rows("video"), name, user_id, "video")
    if not video_rows:
        raise RuntimeError("no video model configured in mediahub_models catalog")

    row = _pick_row(video_rows, name)
    actual_provider = (row.get("actual_provider") or "").lower()

    from app.services.ai.provider_protocols import resolve_generation_protocol

    protocol = resolve_generation_protocol(actual_provider)
    if protocol is None or protocol.generation_family != "jimeng-cli":
        raise RuntimeError(
            f"No video provider implementation for actual_provider="
            f"{actual_provider!r} (catalog row name={row.get('name')!r})"
        )
    provider, actual_model = protocol.build_video_provider(row)
    _stamp_provider_key(provider, actual_provider)
    logger.info(
        "Resolved video provider from catalog: jimeng-cli (model={})", actual_model
    )
    return provider, actual_model
