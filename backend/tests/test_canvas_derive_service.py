"""Canvas derive: load → transform → register into the canvas's scope."""

from __future__ import annotations

import os
import stat
from io import BytesIO
from typing import Any

import pytest
from PIL import Image

from app.services.canvas import canvas_derive_service as cds
from app.services.canvas.canvas_material_source import DeriveInput
from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.image_crop import CropRegion
from app.services.canvas.image_outpaint import Padding


def _png(w: int = 10, h: int = 8) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"registered": [], "loads": [], "next_id": 900}

    async def scope_for_canvas(canvas_id: int, user_id: str) -> int:
        return 42

    async def load(url: str, *, user_id: str, canvas_scope_id: int) -> DeriveInput:
        state["loads"].append((url, user_id, canvas_scope_id))
        return DeriveInput(file_bytes=_png(), mime_type="image/png", label="gen:5")

    async def register(**kwargs: Any) -> dict[str, Any]:
        path = kwargs["source_path"]
        with Image.open(path) as img:
            size = img.size
        state["registered"].append(
            {
                **kwargs,
                "size": size,
                "dir_mode": stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode),
            }
        )
        state["next_id"] += 1
        return {"id": state["next_id"]}

    monkeypatch.setattr(cds, "_scope_for_canvas", scope_for_canvas)
    monkeypatch.setattr(cds, "load_canvas_material", load)
    monkeypatch.setattr(cds, "register_generated_media", register)
    return state


async def test_crop_registers_a_visible_derived_generation(
    wire: dict[str, Any],
) -> None:
    out = await cds.derive_canvas_crop(
        canvas_id=1,
        user_id="u1",
        source_url="/api/v1/resources/9/cover?token=eyJsecret",
        region=CropRegion(x=0.0, y=0.0, width=0.5, height=1.0),
        node_id="n1",
    )
    assert out == cds.CanvasDerivedImage(
        id="901", url="/api/v1/generated-media/901/cover"
    )
    assert wire["loads"] == [("/api/v1/resources/9/cover?token=eyJsecret", "u1", 42)]
    [reg] = wire["registered"]
    assert reg["user_id"] == "u1"
    assert reg["scope_id"] == 42
    assert reg["mime"] == "image/png"
    assert reg["size"] == (5, 8)
    assert reg["dir_mode"] == 0o700
    assert not os.path.exists(reg["source_path"])
    origin = reg["origin"]
    assert origin.kind == "canvas_upload"
    assert origin.canvas_id == 1
    assert origin.node_id == "n1"
    assert origin.derivation_kind == "crop"
    assert origin.params == {
        "role": "derived",
        "op": "crop",
        "derived_from": "gen:5",
        "region": {"x": 0.0, "y": 0.0, "width": 0.5, "height": 1.0},
    }
    assert "eyJsecret" not in repr(origin)


async def test_grid_registers_one_generation_per_tile(wire: dict[str, Any]) -> None:
    out = await cds.derive_canvas_grid(
        canvas_id=1,
        user_id="u1",
        source_url="/api/v1/generated-media/5/cover",
        xs=[0.5],
        ys=[],
    )
    assert [(i.row, i.col, i.id) for i in out] == [(0, 0, "901"), (0, 1, "902")]
    assert [r["size"] for r in wire["registered"]] == [(5, 8), (5, 8)]
    assert wire["registered"][1]["origin"].params == {
        "role": "derived",
        "op": "grid",
        "derived_from": "gen:5",
        "xs": [0.5],
        "ys": [],
        "row": 0,
        "col": 1,
    }


async def test_outpaint_registers_extended_image_with_prompt(
    wire: dict[str, Any],
) -> None:
    out = await cds.derive_canvas_outpaint(
        canvas_id=1,
        user_id="u1",
        source_url="/api/v1/generated-media/5/cover",
        padding=Padding(left=0.5, top=0.0, right=0.5, bottom=0.0),
        prompt="a windswept meadow",
    )
    assert out.url == "/api/v1/generated-media/901/cover"
    [reg] = wire["registered"]
    assert reg["size"] == (20, 8)
    assert reg["origin"].prompt == "a windswept meadow"
    assert reg["origin"].params["padding"] == {
        "left": 0.5,
        "top": 0.0,
        "right": 0.5,
        "bottom": 0.0,
    }
    assert reg["origin"].params["mode"] == "deterministic"


async def test_source_refusal_propagates_before_any_registration(
    wire: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(url: str, *, user_id: str, canvas_scope_id: int) -> DeriveInput:
        raise DeriveError(status_code=404, detail="source image not found")

    monkeypatch.setattr(cds, "load_canvas_material", refuse)
    with pytest.raises(DeriveError) as caught:
        await cds.derive_canvas_crop(
            canvas_id=1,
            user_id="u1",
            source_url="/api/v1/generated-media/5/cover",
            region=CropRegion(x=0.0, y=0.0, width=0.5, height=0.5),
        )
    assert caught.value.status_code == 404
    assert wire["registered"] == []


async def test_invalid_region_fails_before_registration(wire: dict[str, Any]) -> None:
    with pytest.raises(DeriveError) as caught:
        await cds.derive_canvas_grid(
            canvas_id=1,
            user_id="u1",
            source_url="/api/v1/generated-media/5/cover",
            xs=[],
            ys=[],
        )
    assert caught.value.status_code == 400
    assert wire["registered"] == []


async def test_registration_without_id_is_500(
    wire: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_id(**kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(cds, "register_generated_media", no_id)
    with pytest.raises(DeriveError) as caught:
        await cds.derive_canvas_crop(
            canvas_id=1,
            user_id="u1",
            source_url="/api/v1/generated-media/5/cover",
            region=CropRegion(x=0.0, y=0.0, width=0.5, height=0.5),
        )
    assert caught.value.status_code == 500


async def test_unresolvable_canvas_scope_is_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.workflows.canvas_generation as cg

    async def unresolved(canvas_id: int | None, user_id: str | None) -> int:
        raise RuntimeError("scope_unresolved")

    monkeypatch.setattr(cg, "_registration_scope_id", unresolved)
    with pytest.raises(DeriveError) as caught:
        await cds._scope_for_canvas(1, "u1")
    assert caught.value.status_code == 500
