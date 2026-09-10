"""Turn an image reference on a canvas into bytes a derive can transform.

The canvas editors send the URL of the image being edited — never a resources
row id — so an editor works on whatever the canvas shows, wherever it came
from. Two durable shapes exist (``classify_reference_url``) and each carries
its own read rule:

* ``/generated-media/{id}/…`` — the URL is not scoped, so the caller must be
  able to read the row's scope (``can_read_generation_scope``, the rule promote
  uses) OR the row must sit in the canvas's own scope (the write gate on that
  canvas already passed upstream — a project editor on someone else's
  personal-project board is not a member of the owner's personal team).
  Unreadable answers 404, same as missing: a snowflake the caller cannot read
  must not be distinguishable from one that does not exist.
* ``/resources/{id}/cover|file`` — must sit in the CANVAS's scope, the same
  check a canvas generation applies to its references.

Everything else is 422. Every refusal is a typed ``DeriveError``.

The mime handed to a transform is ``transform_mime`` of the bytes, not the
sniffed format verbatim: the transforms encode only JPEG/PNG/WEBP/GIF, and
phone JPEGs carrying an MPF segment sniff as ``MPO``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Mapping
from urllib.parse import urlsplit

from PIL import Image

from app.repositories.conversation_repository import get_conversation_repository
from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.services.canvas.derive_persistence import DeriveError
from app.services.library.generated_media_service import (
    classify_reference_url,
    generated_media_local_path,
    resource_local_path,
)
from app.services.library.generation_access import can_read_generation_scope
from app.services.library.resources_service import _resolve_personal_team_id

#: Upper bound on a source read into memory for one derive.
MAX_SOURCE_BYTES = 64 * 1024 * 1024

_RESOURCE_REASON_STATUS: dict[str, int] = {
    "unknown_shape": 422,
    "not_in_scope": 403,
    "no_image_file": 400,
    "materialize_failed": 502,
}


@dataclass(frozen=True)
class DeriveInput:
    file_bytes: bytes
    mime_type: str
    label: str


async def load_canvas_material(
    url: str, *, user_id: str, canvas_scope_id: int
) -> DeriveInput:
    kind, row_id = classify_reference_url(_reference_path(url))
    if kind == "genmedia" and row_id is not None:
        return await _load_generation(
            row_id, user_id=user_id, canvas_scope_id=canvas_scope_id
        )
    if kind == "resource" and row_id is not None:
        return await _load_resource(row_id, canvas_scope_id=canvas_scope_id)
    raise DeriveError(status_code=422, detail="unsupported image reference")


def _reference_path(url: str) -> str:
    """Drop query and fragment from a RELATIVE reference.

    ``classify_reference_url`` keeps only the path of an absolute own-host URL
    but passes a relative one through whole, and the resource pattern is
    ``$``-anchored — ``/resources/9/cover?token=…`` would classify as unknown.
    """
    text = str(url or "").strip()
    if text.startswith("/") and not text.startswith("//"):
        return urlsplit(text).path
    return text


async def _load_generation(
    gen_id: int, *, user_id: str, canvas_scope_id: int
) -> DeriveInput:
    gen = await GeneratedMediaRepository().get_by_id(gen_id)
    if gen is None or not await _can_read_generation(
        gen, user_id=user_id, canvas_scope_id=canvas_scope_id
    ):
        raise DeriveError(status_code=404, detail="source image not found")
    if gen.get("media_kind") != "image":
        raise DeriveError(status_code=400, detail="source is not an image")
    async with generated_media_local_path(
        f"/api/v1/generated-media/{gen_id}/file", media_kind="image"
    ) as path:
        if path is None:
            raise DeriveError(status_code=502, detail="source image file unavailable")
        data = _read_capped(path)
    return DeriveInput(
        file_bytes=data, mime_type=transform_mime(data), label=f"gen:{gen_id}"
    )


async def _can_read_generation(
    gen: Mapping[str, Any], *, user_id: str, canvas_scope_id: int
) -> bool:
    gen_scope = gen.get("scope_id")
    if gen_scope is not None and int(gen_scope) == canvas_scope_id:
        return True
    return await can_read_generation_scope(
        gen,
        user_id=user_id,
        personal_team_id=int(await _resolve_personal_team_id(user_id)),
        membership=get_conversation_repository(),
    )


async def _load_resource(resource_id: int, *, canvas_scope_id: int) -> DeriveInput:
    async with resource_local_path(
        f"/api/v1/resources/{resource_id}/file",
        scope_id=canvas_scope_id,
        media_kind="image",
    ) as resolution:
        if resolution.path is None:
            reason = resolution.reason or "no_image_file"
            raise DeriveError(
                status_code=_RESOURCE_REASON_STATUS.get(reason, 400),
                detail=f"source image unavailable: {reason}",
            )
        data = _read_capped(resolution.path)
    return DeriveInput(
        file_bytes=data,
        mime_type=transform_mime(data),
        label=f"resource:{resource_id}",
    )


def _read_capped(path: str) -> bytes:
    if os.path.getsize(path) > MAX_SOURCE_BYTES:
        raise DeriveError(status_code=413, detail="source image is too large")
    with open(path, "rb") as fh:
        return fh.read()


def sniff_image_mime(data: bytes) -> str:
    """The encoded format, from the bytes — never from a stored column.

    Truthful, because it also names a derive PRODUCT's registered mime. The
    one rename: ``MPO`` (a JPEG stream plus an MPF segment — iPhone HDR gain
    maps, Android depth frames) is reported as ``image/jpeg``; every JPEG
    decoder reads it, while ``image/mpo`` is not a type a browser renders.
    Undecodable bytes are a 400 ``DeriveError``.
    """
    try:
        with Image.open(BytesIO(data)) as img:
            fmt = img.format or ""
    except Exception as exc:
        raise DeriveError(
            status_code=400, detail="source is not a decodable image"
        ) from exc
    if fmt == "MPO":
        return "image/jpeg"
    mime = Image.MIME.get(fmt)
    if not mime:
        raise DeriveError(status_code=400, detail="source is not a decodable image")
    return mime


#: The encodings every derive transform writes (``image_crop._FORMAT_BY_MIME``).
TRANSFORM_MIMES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)


def transform_mime(data: bytes) -> str:
    """The mime a derive transform should encode a SOURCE with.

    A format the transforms can write passes through; any other decodable
    format (BMP, TIFF, …) becomes ``image/png`` so the product is re-encoded
    instead of refused. Not for products: a product's registered mime must
    describe its actual bytes (``sniff_image_mime``).
    """
    mime = sniff_image_mime(data)
    return mime if mime in TRANSFORM_MIMES else "image/png"
