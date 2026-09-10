"""Canvas derive: any image the canvas shows → crop / grid / outpaint → Tier-1.

    1. ``_scope_for_canvas`` — the scope canvas runs register into.
    2. ``load_canvas_material`` — URL → bytes, with the source's read rule.
    3. the pure transform (``crop_image`` / ``split_image_by_lines`` /
       ``extend_image``) — every tile is cropped before anything registers.
    4. ``register_generated_media`` — one generated_media row per product,
       role ``derived``, visible in the canvas scope's Generated inbox.

A grid whose registration fails mid-loop keeps the tiles already registered —
each is a valid generation on its own — and the caller sees the error.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any, Sequence

from app.services.canvas.canvas_material_source import (
    DeriveInput,
    load_canvas_material,
    sniff_image_mime,
)
from app.services.canvas.crop_derive_service import crop_image
from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.grid_derive_service import split_image_by_lines
from app.services.canvas.image_crop import CropRegion
from app.services.canvas.image_outpaint import Padding
from app.services.canvas.outpaint_derive_service import extend_image
from app.services.library.generated_media_service import (
    GenerationOrigin,
    register_generated_media,
)
from app.services.library.generated_roles import DERIVED, ROLE_KEY


@dataclass(frozen=True)
class CanvasDerivedImage:
    id: str
    url: str
    row: int | None = None
    col: int | None = None


@dataclass(frozen=True)
class _Target:
    canvas_id: int
    user_id: str
    node_id: str | None
    scope_id: int


async def _scope_for_canvas(canvas_id: int, user_id: str) -> int:
    """The canvas run registration scope. Lazy import: the workflow module is
    heavy and this service is imported at router load."""
    from app.workflows.canvas_generation import _registration_scope_id

    try:
        return await _registration_scope_id(canvas_id, user_id)
    except RuntimeError as exc:
        raise DeriveError(
            status_code=500, detail="canvas scope could not be resolved"
        ) from exc


async def _prepare(
    *, canvas_id: int, user_id: str, source_url: str, node_id: str | None
) -> tuple[_Target, DeriveInput]:
    scope_id = await _scope_for_canvas(canvas_id, user_id)
    source = await load_canvas_material(
        source_url, user_id=user_id, canvas_scope_id=scope_id
    )
    target = _Target(
        canvas_id=canvas_id, user_id=user_id, node_id=node_id, scope_id=scope_id
    )
    return target, source


async def _register(
    target: _Target,
    source: DeriveInput,
    image_bytes: bytes,
    *,
    op: str,
    op_params: dict[str, Any],
    prompt: str | None = None,
    row: int | None = None,
    col: int | None = None,
) -> CanvasDerivedImage:
    mime = sniff_image_mime(image_bytes)
    # Private 0700 dir + random name + exclusive create; register copies the
    # file before returning, so the directory can go when this block exits.
    with tempfile.TemporaryDirectory(prefix="canvas_derive_") as tmp_dir:
        fd, tmp_path = tempfile.mkstemp(dir=tmp_dir, suffix=".img")
        with os.fdopen(fd, "wb") as out:
            out.write(image_bytes)
        row_data = await register_generated_media(
            user_id=target.user_id,
            scope_id=target.scope_id,
            source_path=tmp_path,
            mime=mime,
            origin=GenerationOrigin(
                kind="canvas_upload",
                canvas_id=target.canvas_id,
                node_id=target.node_id,
                prompt=prompt,
                derivation_kind=op,
                params={
                    ROLE_KEY: DERIVED,
                    "op": op,
                    "derived_from": source.label,
                    **op_params,
                },
            ),
        )
    gen_id = row_data.get("id")
    if gen_id is None:
        raise DeriveError(status_code=500, detail="derived image registration failed")
    return CanvasDerivedImage(
        id=str(gen_id),
        url=f"/api/v1/generated-media/{gen_id}/cover",
        row=row,
        col=col,
    )


async def derive_canvas_crop(
    *,
    canvas_id: int,
    user_id: str,
    source_url: str,
    region: CropRegion,
    node_id: str | None = None,
) -> CanvasDerivedImage:
    target, source = await _prepare(
        canvas_id=canvas_id, user_id=user_id, source_url=source_url, node_id=node_id
    )
    image = crop_image(source.file_bytes, source.mime_type, region)
    return await _register(
        target,
        source,
        image,
        op="crop",
        op_params={
            "region": {
                "x": region.x,
                "y": region.y,
                "width": region.width,
                "height": region.height,
            }
        },
    )


async def derive_canvas_grid(
    *,
    canvas_id: int,
    user_id: str,
    source_url: str,
    xs: Sequence[float],
    ys: Sequence[float],
    node_id: str | None = None,
) -> list[CanvasDerivedImage]:
    target, source = await _prepare(
        canvas_id=canvas_id, user_id=user_id, source_url=source_url, node_id=node_id
    )
    tiles = split_image_by_lines(source.file_bytes, source.mime_type, xs=xs, ys=ys)
    results: list[CanvasDerivedImage] = []
    for tile in tiles:
        results.append(
            await _register(
                target,
                source,
                tile.image_bytes,
                op="grid",
                op_params={
                    "xs": list(xs),
                    "ys": list(ys),
                    "row": tile.row,
                    "col": tile.col,
                },
                row=tile.row,
                col=tile.col,
            )
        )
    return results


async def derive_canvas_outpaint(
    *,
    canvas_id: int,
    user_id: str,
    source_url: str,
    padding: Padding,
    prompt: str | None = None,
    mode: str = "deterministic",
    node_id: str | None = None,
) -> CanvasDerivedImage:
    target, source = await _prepare(
        canvas_id=canvas_id, user_id=user_id, source_url=source_url, node_id=node_id
    )
    image = await extend_image(
        source.file_bytes,
        source.mime_type,
        padding,
        prompt=prompt,
        mode=mode,
        label=source.label,
    )
    return await _register(
        target,
        source,
        image,
        op="outpaint",
        op_params={
            "padding": {
                "left": padding.left,
                "top": padding.top,
                "right": padding.right,
                "bottom": padding.bottom,
            },
            "mode": mode,
        },
        prompt=prompt,
    )
