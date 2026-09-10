"""Canvas-scoped image derive — crop / grid / outpaint any image the canvas shows.

  POST /api/v1/canvases/{canvas_id}/derive-crop
  POST /api/v1/canvases/{canvas_id}/derive-grid
  POST /api/v1/canvases/{canvas_id}/derive-outpaint

Write access is checked on the CANVAS (``_gate_canvas_write``); read access on
the SOURCE image (``canvas_material_source``). Products are generated_media rows
in the canvas's own scope. Replaces the ``/resources/{id}/derive-*`` family for
the canvas editors, which required every image to be promoted into the library
first.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Sequence

from fastapi import APIRouter, Depends, HTTPException, Path
from loguru import logger

from app.api.canvases_router import _gate_canvas_write
from app.core.deps import AuthDep
from app.db.scope import Scope, request_scope
from app.schemas.canvas_material_derive_schema import (
    CanvasCropDeriveRequest,
    CanvasGridDeriveRequest,
    CanvasOutpaintDeriveRequest,
)
from app.services.canvas import canvas_derive_service as derive
from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.image_crop import CropRegion
from app.services.canvas.image_outpaint import Padding
from app.services.modules.gate import require_module

router = APIRouter(dependencies=[Depends(require_module("projects"))])

_CANVAS_ID_PATTERN = r"^\d{1,20}$"


def _ok(images: Sequence[derive.CanvasDerivedImage]) -> dict:
    return {
        "success": True,
        "data": {
            "images": [
                {
                    "id": image.id,
                    "url": image.url,
                    "kind": "image",
                    "row": image.row,
                    "col": image.col,
                }
                for image in images
            ]
        },
    }


async def _run(
    canvas_id: str,
    auth: AuthDep,
    op: str,
    work: Callable[[], Awaitable[list[derive.CanvasDerivedImage]]],
) -> dict:
    await _gate_canvas_write(canvas_id, auth)
    # Reading a resource source touches the scoped ``resources`` model, which
    # requires an ambient Scope at the request boundary.
    async with request_scope(Scope(user_id=str(auth.user_id))):
        try:
            return _ok(await work())
        except DeriveError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"canvas {canvas_id} derive-{op} failed: {exc!r}")
            raise HTTPException(
                status_code=500, detail=f"Failed to derive {op}"
            ) from exc


@router.post("/canvases/{canvas_id}/derive-crop")
async def derive_crop_endpoint(
    body: CanvasCropDeriveRequest,
    auth: AuthDep,
    canvas_id: str = Path(..., pattern=_CANVAS_ID_PATTERN),
) -> dict:
    async def work() -> list[derive.CanvasDerivedImage]:
        image = await derive.derive_canvas_crop(
            canvas_id=int(canvas_id),
            user_id=str(auth.user_id),
            source_url=body.source_url,
            node_id=body.node_id,
            region=CropRegion(
                x=body.region.x,
                y=body.region.y,
                width=body.region.width,
                height=body.region.height,
            ),
        )
        return [image]

    return await _run(canvas_id, auth, "crop", work)


@router.post("/canvases/{canvas_id}/derive-grid")
async def derive_grid_endpoint(
    body: CanvasGridDeriveRequest,
    auth: AuthDep,
    canvas_id: str = Path(..., pattern=_CANVAS_ID_PATTERN),
) -> dict:
    async def work() -> list[derive.CanvasDerivedImage]:
        return await derive.derive_canvas_grid(
            canvas_id=int(canvas_id),
            user_id=str(auth.user_id),
            source_url=body.source_url,
            node_id=body.node_id,
            xs=body.xs,
            ys=body.ys,
        )

    return await _run(canvas_id, auth, "grid", work)


@router.post("/canvases/{canvas_id}/derive-outpaint")
async def derive_outpaint_endpoint(
    body: CanvasOutpaintDeriveRequest,
    auth: AuthDep,
    canvas_id: str = Path(..., pattern=_CANVAS_ID_PATTERN),
) -> dict:
    async def work() -> list[derive.CanvasDerivedImage]:
        image = await derive.derive_canvas_outpaint(
            canvas_id=int(canvas_id),
            user_id=str(auth.user_id),
            source_url=body.source_url,
            node_id=body.node_id,
            padding=Padding(
                left=body.left, top=body.top, right=body.right, bottom=body.bottom
            ),
            prompt=body.prompt,
            mode=body.mode,
        )
        return [image]

    return await _run(canvas_id, auth, "outpaint", work)
