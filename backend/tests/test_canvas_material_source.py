"""Canvas image reference → bytes, with the SOURCE's read rule applied."""

from __future__ import annotations

import contextlib
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.services.canvas import canvas_material_source as cms
from app.services.canvas.crop_derive_service import crop_image
from app.services.canvas.derive_persistence import DeriveError
from app.services.canvas.grid_derive_service import split_image_by_lines
from app.services.canvas.image_crop import CropRegion
from app.services.library.generated_media_service import ResourceRefResolution


def _png_file(tmp_path: Path) -> str:
    path = tmp_path / "source.png"
    Image.new("RGB", (10, 8), (1, 2, 3)).save(path, format="PNG")
    return str(path)


class FakeGenRepo:
    def __init__(self, rows: dict[int, dict[str, Any]]) -> None:
        self.rows = rows

    async def get_by_id(self, gen_id: int) -> dict[str, Any] | None:
        return self.rows.get(gen_id)


class FakeMembership:
    def __init__(self, teams: set[int]) -> None:
        self.teams = teams

    async def is_team_member(self, *, team_id: int, user_id: str) -> bool:
        return team_id in self.teams


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    png = _png_file(tmp_path)
    state: dict[str, Any] = {
        "rows": {5: {"id": "5", "scope_id": "7", "media_kind": "image"}},
        "teams": set(),
        "gen_path": png,
        "gen_urls": [],
        "resource": ResourceRefResolution(path=png),
        "resource_calls": [],
    }

    async def personal_team(user_id: str) -> str:
        return "7"

    @contextlib.asynccontextmanager
    async def gen_path(url: str, *, media_kind: str = "image"):
        state["gen_urls"].append(url)
        yield state["gen_path"]

    @contextlib.asynccontextmanager
    async def res_path(url: str, *, scope_id: int, media_kind: str = "image"):
        state["resource_calls"].append((url, scope_id))
        yield state["resource"]

    monkeypatch.setattr(
        cms, "GeneratedMediaRepository", lambda: FakeGenRepo(state["rows"])
    )
    monkeypatch.setattr(cms, "_resolve_personal_team_id", personal_team)
    monkeypatch.setattr(
        cms, "get_conversation_repository", lambda: FakeMembership(state["teams"])
    )
    monkeypatch.setattr(cms, "generated_media_local_path", gen_path)
    monkeypatch.setattr(cms, "resource_local_path", res_path)
    return state


async def _load(url: str) -> cms.DeriveInput:
    return await cms.load_canvas_material(url, user_id="u1", canvas_scope_id=42)


async def test_generation_in_personal_scope_loads(wire: dict[str, Any]) -> None:
    got = await _load("/api/v1/generated-media/5/cover?v=2")
    assert got.mime_type == "image/png"
    assert got.label == "gen:5"
    assert got.file_bytes.startswith(b"\x89PNG")
    assert wire["gen_urls"] == ["/api/v1/generated-media/5/file"]


async def test_generation_in_team_scope_loads_for_member(wire: dict[str, Any]) -> None:
    wire["rows"][5]["scope_id"] = "99"
    wire["teams"].add(99)
    assert (await _load("/api/v1/generated-media/5/cover")).label == "gen:5"


async def test_generation_in_the_canvas_scope_loads_for_a_non_member(
    wire: dict[str, Any],
) -> None:
    """A project editor on someone else's personal-project canvas passed the
    canvas write gate upstream; the board's own generations live in the
    owner's personal team, which the editor is not a member of."""
    wire["rows"][5]["scope_id"] = "42"
    assert (await _load("/api/v1/generated-media/5/cover")).label == "gen:5"


async def test_generation_without_a_scope_is_404(wire: dict[str, Any]) -> None:
    wire["rows"][5]["scope_id"] = None
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/cover")
    assert caught.value.status_code == 404


async def test_unreadable_video_is_404_not_400(wire: dict[str, Any]) -> None:
    """Readability is checked before the media kind: an unreadable row must
    not reveal what it is."""
    wire["rows"][5].update(scope_id="99", media_kind="video")
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/stream")
    assert caught.value.status_code == 404


async def test_unreadable_generation_is_404(wire: dict[str, Any]) -> None:
    wire["rows"][5]["scope_id"] = "99"
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/cover")
    assert caught.value.status_code == 404


async def test_missing_generation_is_404(wire: dict[str, Any]) -> None:
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/6/cover")
    assert caught.value.status_code == 404


async def test_video_generation_is_400(wire: dict[str, Any]) -> None:
    wire["rows"][5]["media_kind"] = "video"
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/stream")
    assert caught.value.status_code == 400


async def test_generation_file_unavailable_is_502(wire: dict[str, Any]) -> None:
    wire["gen_path"] = None
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/cover")
    assert caught.value.status_code == 502


async def test_resource_reference_is_checked_against_canvas_scope(
    wire: dict[str, Any],
) -> None:
    got = await _load("/api/v1/resources/9/cover?token=abc")
    assert got.label == "resource:9"
    assert wire["resource_calls"] == [("/api/v1/resources/9/file", 42)]


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        ("not_in_scope", 403),
        ("no_image_file", 400),
        ("materialize_failed", 502),
    ],
)
async def test_resource_refusals_are_typed(
    wire: dict[str, Any], reason: str, status: int
) -> None:
    wire["resource"] = ResourceRefResolution(reason=reason)
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/resources/9/cover")
    assert caught.value.status_code == status
    assert reason in caught.value.detail


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example/x.png",
        "blob:https://app.nous.ink/1",
        "",
        "data:image/png;base64,AAAA",
    ],
)
async def test_unsupported_shapes_are_422(wire: dict[str, Any], url: str) -> None:
    with pytest.raises(DeriveError) as caught:
        await _load(url)
    assert caught.value.status_code == 422


async def test_undecodable_bytes_are_400(wire: dict[str, Any], tmp_path: Path) -> None:
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not an image")
    wire["gen_path"] = str(junk)
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/cover")
    assert caught.value.status_code == 400


async def test_oversized_source_is_413(
    wire: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cms, "MAX_SOURCE_BYTES", 10)
    with pytest.raises(DeriveError) as caught:
        await _load("/api/v1/generated-media/5/cover")
    assert caught.value.status_code == 413


def _mpo_bytes() -> bytes:
    """A phone-camera shape: a JPEG stream with an MPF segment (HDR gain map,
    Android depth frame), which Pillow reports as ``MPO``."""
    buf = BytesIO()
    Image.new("RGB", (10, 8), (1, 2, 3)).save(
        buf,
        format="MPO",
        save_all=True,
        append_images=[Image.new("RGB", (10, 8), (4, 5, 6))],
    )
    return buf.getvalue()


def _write(tmp_path: Path, name: str, data: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def _encoded(fmt: str) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (10, 8), (1, 2, 3)).save(buf, format=fmt)
    return buf.getvalue()


def _format_of(data: bytes) -> str:
    with Image.open(BytesIO(data)) as img:
        return img.format or ""


async def test_mpo_source_is_handed_to_transforms_as_jpeg(
    wire: dict[str, Any], tmp_path: Path
) -> None:
    mpo = _mpo_bytes()
    assert _format_of(mpo) == "MPO"
    wire["gen_path"] = _write(tmp_path, "phone.jpg", mpo)

    got = await _load("/api/v1/generated-media/5/cover")

    assert got.mime_type == "image/jpeg"
    cropped = crop_image(
        got.file_bytes, got.mime_type, CropRegion(x=0, y=0, width=0.5, height=1)
    )
    assert _format_of(cropped) == "JPEG"
    tiles = split_image_by_lines(got.file_bytes, got.mime_type, xs=[0.5], ys=[])
    assert [_format_of(t.image_bytes) for t in tiles] == ["JPEG", "JPEG"]


@pytest.mark.parametrize("fmt", ["BMP", "TIFF"])
async def test_exotic_source_is_handed_to_transforms_as_png(
    wire: dict[str, Any], tmp_path: Path, fmt: str
) -> None:
    wire["resource"] = ResourceRefResolution(
        path=_write(tmp_path, f"source.{fmt.lower()}", _encoded(fmt))
    )

    got = await _load("/api/v1/resources/9/cover")

    assert got.mime_type == "image/png"
    cropped = crop_image(
        got.file_bytes, got.mime_type, CropRegion(x=0, y=0, width=0.5, height=1)
    )
    assert _format_of(cropped) == "PNG"


@pytest.mark.parametrize(
    ("fmt", "mime"),
    [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
        ("WEBP", "image/webp"),
        ("GIF", "image/gif"),
    ],
)
async def test_transform_ready_formats_pass_through(
    wire: dict[str, Any], tmp_path: Path, fmt: str, mime: str
) -> None:
    wire["gen_path"] = _write(tmp_path, "source.img", _encoded(fmt))
    assert (await _load("/api/v1/generated-media/5/cover")).mime_type == mime


def test_sniff_reports_mpo_as_jpeg_and_keeps_other_formats_truthful() -> None:
    """``sniff_image_mime`` also names a derive PRODUCT's registered mime, so
    it must stay truthful: MPO *is* a JPEG stream (``image/mpo`` is not a type
    a browser renders), but an exotic product is not silently called PNG."""
    assert cms.sniff_image_mime(_mpo_bytes()) == "image/jpeg"
    assert cms.sniff_image_mime(_encoded("BMP")) == "image/bmp"
