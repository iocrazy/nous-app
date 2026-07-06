"""Ark (Volcengine / 豆包) text-to-image provider.

Ark exposes an OpenAI-compatible synchronous image-generation endpoint
(``POST {base_url}/images/generations``) that returns the produced image url
inline — there is no async task / polling handle. So ``generate`` returns the
final :class:`ImageGenResult` directly and ``check_status`` is not applicable.

Constructed from a resolved ``mediahub_models`` catalog row (see
``db_registry.resolve_image_provider``): ``api_key`` (revealed/decrypted by the
repository), ``base_url`` (e.g. ``https://ark.cn-beijing.volces.com/api/v3``),
and ``default_model`` (the catalog ``actual_model``, e.g. a doubao-seedream id).
"""

from __future__ import annotations

from typing import Optional, Tuple

import httpx
from loguru import logger

from app.services.media.parsers.video_providers.base import (
    BaseImageProvider,
    ImageGenResult,
    TaskStatus,
)

# aspect_ratio → an Ark seedream-supported size. Ark rejects arbitrary sizes, so
# the caller's aspect_ratio maps onto a known-good WxH; an explicit ``size``
# kwarg (when a caller passes one) wins over this map.
_ASPECT_TO_SIZE = {
    "16:9": "1280x720",
    "9:16": "720x1280",
    "1:1": "1024x1024",
    "4:3": "1152x864",
    "3:4": "864x1152",
}
_DEFAULT_SIZE = "1024x1024"

_TIMEOUT_SECONDS = 60.0


def _resolve_size(size: Optional[str], aspect_ratio: Optional[str]) -> str:
    """An explicit ``size`` wins; else map ``aspect_ratio`` onto a known Ark
    size, defaulting to a square when the ratio is unknown/missing."""
    if size:
        return size
    return _ASPECT_TO_SIZE.get(aspect_ratio or "", _DEFAULT_SIZE)


def _parse_dims(size: str) -> Tuple[Optional[int], Optional[int]]:
    """Best-effort ``"1280x720"`` → ``(1280, 720)``; ``(None, None)`` on any
    unexpected shape (never fatal — dims are decorative on ImageGenResult)."""
    try:
        width_str, height_str = size.lower().split("x", 1)
        return int(width_str), int(height_str)
    except (ValueError, AttributeError):
        return None, None


class ArkImageProvider(BaseImageProvider):
    """OpenAI-compatible text-to-image against the Ark /images/generations API."""

    def __init__(self, api_key: str, base_url: str, default_model: str) -> None:
        if not api_key:
            raise ValueError("ArkImageProvider requires an api_key")
        if not base_url:
            raise ValueError("ArkImageProvider requires a base_url")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model

    async def generate(self, prompt: str, model: str, **kwargs) -> ImageGenResult:
        """POST the prompt to Ark and return the produced image url.

        ``kwargs`` honored: ``size`` (explicit WxH, wins) or ``aspect_ratio``
        (mapped to an Ark size). ``reference_image_url`` is accepted for
        signature parity with the other image providers but not sent — the
        /images/generations endpoint is pure text-to-image.
        """
        used_model = model or self._default_model
        size = _resolve_size(kwargs.get("size"), kwargs.get("aspect_ratio"))
        url = f"{self._base_url}/images/generations"
        payload = {
            "model": used_model,
            "prompt": prompt,
            "size": size,
            "response_format": "url",
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload, headers=headers)

        if response.status_code != 200:
            snippet = response.text[:500]
            logger.error(
                "Ark image generation failed: HTTP {} — {}",
                response.status_code,
                snippet,
            )
            raise RuntimeError(
                f"Ark image generation failed (HTTP {response.status_code}): {snippet}"
            )

        body = response.json()
        data = body.get("data") or []
        image_url = data[0].get("url") if data and isinstance(data[0], dict) else None
        if not image_url:
            raise RuntimeError(
                f"Ark image generation returned no url (model={used_model})"
            )

        width, height = _parse_dims(size)
        metadata: dict = {"size": size}
        revised = data[0].get("revised_prompt")
        if revised:
            metadata["revised_prompt"] = revised

        return ImageGenResult(
            image_url=image_url,
            width=width,
            height=height,
            provider="ark",
            model=used_model,
            metadata=metadata,
        )

    async def check_status(self, task_id: str) -> TaskStatus:
        """Not applicable — Ark's /images/generations is synchronous, so
        ``generate`` already returns the final result inline (no task handle to
        poll)."""
        raise NotImplementedError(
            "ArkImageProvider generation is synchronous; check_status is not "
            "applicable"
        )

    def list_models(self) -> list[str]:
        return [self._default_model]
