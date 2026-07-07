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

Image callers use the ``BaseImageProvider.generate`` contract, so the CLI
provider is wrapped in ``_JimengImageAdapter`` (returns an ``ImageGenResult``
with ``image_path`` set — a local file, not a URL). Video callers use
``JimengCliProvider.generate_video`` directly (only the CLI has a wired video
path today), so ``resolve_video_provider`` returns the provider as-is.
"""

from __future__ import annotations

from typing import Optional, Tuple

from loguru import logger

from app.services.media.parsers.video_providers.ark_image import ArkImageProvider
from app.services.media.parsers.video_providers.base import (
    BaseImageProvider,
    ImageGenResult,
    TaskStatus,
)
from app.services.media.parsers.video_providers.jimeng_cli import JimengCliProvider

_ARK_PROVIDERS = {"doubao", "ark"}
_JIMENG_PROVIDERS = {"jimeng-cli", "jimeng"}


class _JimengImageAdapter(BaseImageProvider):
    """Adapts JimengCliProvider onto the BaseImageProvider ``generate`` contract.

    ``generate`` returns an ``ImageGenResult`` whose ``image_path`` is the local
    file the CLI produced (``image_url`` stays empty — there is no URL). The
    downstream persist step ingests the local file via ``source_path``.
    """

    def __init__(self, provider: JimengCliProvider) -> None:
        self._provider = provider

    async def generate(self, prompt: str, model: str, **kwargs) -> ImageGenResult:
        result = await self._provider.generate_image(
            prompt=prompt,
            aspect=kwargs.get("aspect_ratio") or "",
            model_version=model or None,
        )
        return ImageGenResult(
            image_url="",
            image_path=result.local_path,
            provider="jimeng-cli",
            model=model or "",
            metadata={"mime": result.mime, **(result.raw or {})},
        )

    async def check_status(self, task_id: str) -> TaskStatus:
        raise NotImplementedError(
            "JimengCliProvider generation is synchronous; check_status is n/a"
        )

    def list_models(self) -> list[str]:
        return []


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
        jimeng = next(
            (
                r
                for r in rows
                if (r.get("actual_provider") or "").lower() in _JIMENG_PROVIDERS
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


async def resolve_image_provider(
    name: Optional[str] = None,
) -> Tuple[BaseImageProvider, str]:
    """Resolve an image provider from the ``mediahub_models`` catalog.

    Returns ``(provider, actual_model)``. Dispatches on ``actual_provider``;
    when several image rows are enabled and no explicit ``name`` matches, the
    jimeng-cli row is preferred (CLI is the primary generator).

    Raises:
        RuntimeError: no enabled image model, or the resolved row's
            ``actual_provider`` has no wired implementation.
    """
    image_rows = await _enabled_rows("image")
    if not image_rows:
        raise RuntimeError("no image model configured in mediahub_models catalog")

    row = _pick_row(image_rows, name)
    actual_provider = (row.get("actual_provider") or "").lower()
    actual_model = row.get("actual_model") or ""

    if actual_provider in _JIMENG_PROVIDERS:
        logger.info(
            "Resolved image provider from catalog: jimeng-cli (model={})", actual_model
        )
        return _JimengImageAdapter(JimengCliProvider()), actual_model

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


async def resolve_video_provider(
    name: Optional[str] = None,
) -> Tuple[JimengCliProvider, str]:
    """Resolve a video provider from the ``mediahub_models`` catalog.

    Returns ``(provider, actual_model)``. Only the jimeng-cli CLI has a wired
    video path today; callers use ``provider.generate_video(...)`` directly.

    Raises:
        RuntimeError: no enabled video model, or the resolved row's
            ``actual_provider`` has no wired video implementation.
    """
    video_rows = await _enabled_rows("video")
    if not video_rows:
        raise RuntimeError("no video model configured in mediahub_models catalog")

    row = _pick_row(video_rows, name)
    actual_provider = (row.get("actual_provider") or "").lower()
    actual_model = row.get("actual_model") or ""

    if actual_provider in _JIMENG_PROVIDERS:
        logger.info(
            "Resolved video provider from catalog: jimeng-cli (model={})", actual_model
        )
        return JimengCliProvider(), actual_model

    raise RuntimeError(
        f"No video provider implementation for actual_provider="
        f"{actual_provider!r} (catalog row name={row.get('name')!r})"
    )
