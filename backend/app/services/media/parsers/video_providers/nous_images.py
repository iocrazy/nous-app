"""nous-engine image bridge — the self-hosted ``/images/generations`` endpoint.

nous-engine publishes ComfyUI-style workflows as OpenAI-compatible "models".
Two request shapes share one endpoint:

- super-resolution (``studio-upscale``)::

      {"model": "<service>", "image": "data:image/png;base64,...",
       "resolution": <int target short side>}

- text-to-image::

      {"model": "<service>", "prompt": "..."}

The response is ``{"created": int, "data": [{"url": "<signed link>"}]}``. The
url carries its own ``token``/``expires`` query and is fetched WITHOUT the
Bearer key. Failures are OpenAI-shaped ``{"error": {"message", "type",
"code"}}`` — 404 ``model_not_found`` means the key is not authorised for that
service, 402 is quota, 503 is "not ready" (see
``docs/runbook/nous-engine-image-bridge.md``).

Model weights and workflows live in nous-engine; this side only speaks the
protocol (2026-09-16 用户原则 — nous-app 是业务层).
"""

from __future__ import annotations

import base64
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.services.media.parsers.video_providers.base import (
    BaseImageProvider,
    GenResult,
    ImageGenResult,
    TaskStatus,
)

# The canvas speaks in "2k / 4k / 8k" (the dreamina vocabulary the upscale
# button was built on); nous-engine speaks in target SHORT SIDE pixels. One
# table, so the mapping is reviewable in a single place.
UPSCALE_SHORT_SIDE: dict[str, int] = {"2k": 1440, "4k": 2160, "8k": 4320}

# SeedVR2 on a single GPU can take minutes for an 8k pass; the engine answers
# synchronously, so the client must outwait it rather than abandon a job the
# engine keeps running.
_TIMEOUT_SECONDS = 600.0

# Must match ``scratch_reaper.SCRATCH_DIR_PREFIXES`` so the route can reclaim
# the directory once the file is copied into storage.
SCRATCH_PREFIX = "nousimg_"

_EXT_FOR_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class NousEngineImageError(RuntimeError):
    """nous-engine refused or failed an image request.

    ``status`` is the HTTP status (``None`` when the failure is not an HTTP
    answer, e.g. a response without a url) and ``code`` is the engine's
    ``error.code`` (``model_not_found`` / quota / not-ready ...) so a caller can
    tell "key not authorised" from "engine busy" without parsing prose.
    """

    def __init__(self, message: str, *, status: int | None, code: str | None):
        self.status = status
        self.code = code
        super().__init__(message)


def upscale_short_side(resolution: str) -> int:
    """``"2k"`` → 1440 etc. Unknown values raise — never a silent default."""
    key = (resolution or "").strip().lower()
    if key not in UPSCALE_SHORT_SIDE:
        raise ValueError(
            f"unsupported upscale resolution {resolution!r}; "
            f"expected one of {sorted(UPSCALE_SHORT_SIDE)}"
        )
    return UPSCALE_SHORT_SIDE[key]


def image_data_uri(path: str) -> str:
    """Read ``path`` into a ``data:<mime>;base64,...`` URI."""
    mime = mimetypes.guess_type(path)[0] or "image/png"
    if not mime.startswith("image/"):
        raise ValueError(f"not an image file: {path!r} (guessed {mime})")
    encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _error_from(response: httpx.Response) -> NousEngineImageError:
    code: str | None = None
    message = response.text[:500]
    try:
        err = response.json().get("error")
    except ValueError:
        err = None
    if isinstance(err, dict):
        code = err.get("code") or err.get("type")
        message = err.get("message") or message
    return NousEngineImageError(
        f"nous-engine image request failed (HTTP {response.status_code}, "
        f"code={code}): {message}",
        status=response.status_code,
        code=code,
    )


def _first_url(body: Any, model: str) -> str:
    data = body.get("data") if isinstance(body, dict) else None
    first = data[0] if isinstance(data, list) and data else None
    url = first.get("url") if isinstance(first, dict) else None
    if not url:
        raise NousEngineImageError(
            f"nous-engine returned no image url (model={model})",
            status=None,
            code="no_url",
        )
    return str(url)


def _write_private(content: bytes, mime: str) -> str:
    """Persist ``content`` under a 0700 scratch dir with an exclusively-created
    0600 file — no predictable world-readable path (CLAUDE.md 防御模式)."""
    directory = tempfile.mkdtemp(prefix=SCRATCH_PREFIX)  # mode 0700
    path = os.path.join(directory, f"upscaled{_EXT_FOR_MIME.get(mime, '.png')}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(content)
    return path


class NousImagesProvider(BaseImageProvider):
    """OpenAI-compatible image client for nous-engine's published workflows."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_model: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("NousImagesProvider requires a base_url")
        if not api_key:
            raise ValueError("NousImagesProvider requires an api_key")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        # Test seam: httpx.MockTransport. Production uses the default transport.
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, transport=self._transport)

    async def _post(self, client: httpx.AsyncClient, payload: dict) -> Any:
        response = await client.post(
            f"{self._base_url}/images/generations",
            json=payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if response.status_code // 100 != 2:
            error = _error_from(response)
            logger.error("{}", error)
            raise error
        return response.json()

    async def upscale_image(
        self,
        *,
        image_path: str,
        resolution: str = "2k",
        model: str | None = None,
    ) -> GenResult:
        """Super-resolve ``image_path`` and return the result as a local file.

        ``resolution`` is the canvas vocabulary (2k/4k/8k), mapped onto the
        engine's short-side pixels by :data:`UPSCALE_SHORT_SIDE`.
        """
        used_model = model or self._default_model
        short_side = upscale_short_side(resolution)
        payload = {
            "model": used_model,
            "image": image_data_uri(image_path),
            "resolution": short_side,
        }
        async with self._client() as client:
            body = await self._post(client, payload)
            url = _first_url(body, used_model)
            # Signed link: no Authorization header — the key never leaves for
            # a URL the engine chose.
            download = await client.get(url)
        if download.status_code // 100 != 2:
            raise NousEngineImageError(
                f"nous-engine image download failed (HTTP {download.status_code})",
                status=download.status_code,
                code="download_failed",
            )
        mime = (download.headers.get("content-type") or "image/png").split(";")[0]
        if not mime.startswith("image/"):
            mime = "image/png"
        local_path = _write_private(download.content, mime)
        return GenResult(
            local_path=local_path,
            mime=mime,
            raw={"model": used_model, "resolution": short_side},
        )

    async def generate(self, prompt: str, model: str, **kwargs) -> ImageGenResult:
        """Text-to-image: ``{"model", "prompt"}`` → the signed image url.

        ``aspect_ratio`` / reference images are NOT sent: the published
        workflows take no such inputs today, and the protocol declares no
        ratio/refs capability so ``reconcile`` drops them loudly upstream.
        """
        used_model = model or self._default_model
        async with self._client() as client:
            body = await self._post(client, {"model": used_model, "prompt": prompt})
        return ImageGenResult(
            image_url=_first_url(body, used_model),
            provider="nous",
            model=used_model,
        )

    async def check_status(self, task_id: str) -> TaskStatus:
        raise NotImplementedError(
            "nous-engine /images/generations is synchronous; check_status is n/a"
        )

    def list_models(self) -> list[str]:
        return [self._default_model]
