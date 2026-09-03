"""The resource → local-file reference bridge (asset-library P4 Task 3).

Before this bridge the canvas generation chain accepted ONE durable reference
shape, ``/api/v1/generated-media/{id}/…``. The asset library's reference
channel is ``resources`` (``asset_files.resource_id``; the bundle protocol
hands out ``reference_resource_ids``), so an asset reference reached
``eff.refs`` and then vanished — not in the picture, not in ``dropped_knobs``,
not on the node. These tests pin the two halves of the fix: the classifier
that recognises both shapes and refuses everything else, and the resolver that
turns a resource URL into a readable local file or SAYS WHY it could not.
"""

from __future__ import annotations

import os

import pytest

import app.repositories.asset_relations_repository as relations_mod
import app.services.library.generated_media_service as gm_svc
from app.services.library import media_storage as ms
from app.services.library.generated_media_service import (
    CANVAS_REFERENCE_READ_REASON,
    classify_reference_url,
    resource_local_path,
    resource_reference_reason,
)

SCOPE = 727145299382534100


# ── the classifier ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("/api/v1/generated-media/5/cover", ("genmedia", 5)),
        ("/api/v1/generated-media/5/stream", ("genmedia", 5)),
        ("/api/v1/generated-media/5/file", ("genmedia", 5)),
        ("/api/v1/resources/91/cover", ("resource", 91)),
        ("/api/v1/resources/91/file", ("resource", 91)),
    ],
)
def test_both_durable_shapes_are_recognised(url, expected):
    assert classify_reference_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # A foreign host, in the three casings a hand-rolled startswith check
        # lets through. The daemon branch hands accepted urls to the USER's
        # machine to fetch, so "somebody else's host" must never classify as
        # one of ours.
        "https://cdn.example/api/v1/resources/91/cover",
        "HTTPS://cdn.example/api/v1/resources/91/cover",
        "http://evil.example/api/v1/generated-media/5/cover",
        # Protocol-relative: looks like a path, is not one.
        "//evil.example/api/v1/resources/91/cover",
        # Right prefix, wrong endpoint / trailing segments.
        "/api/v1/resources/91/tags",
        "/api/v1/resources/91/cover/extra",
        "/api/v1/resources/cover",
        "/api/v1/resources/91",
        # Neither shape.
        "/api/v1/media/91/audio",
        "",
        None,
    ],
)
def test_everything_else_is_unknown(url):
    assert classify_reference_url(url) == ("unknown", None)


def test_our_own_absolute_url_is_still_ours(monkeypatch):
    """An absolute url is accepted when the host is one WE mint urls on —
    otherwise this bridge would refuse the very shape ``_reference_url``
    builds out of ``MEDIA_PUBLIC_URL``."""
    monkeypatch.setattr(gm_svc.settings, "MEDIA_PUBLIC_URL", "https://api.nous.test")
    assert classify_reference_url(
        "https://api.nous.test/api/v1/resources/91/cover"
    ) == ("resource", 91)
    assert classify_reference_url(
        "https://other.nous.test/api/v1/resources/91/cover"
    ) == ("unknown", None)


def test_public_api_base_is_not_a_settings_field(monkeypatch):
    """Ground truth for the two tests below, asserted rather than assumed.

    ``PUBLIC_API_BASE`` appears in two ``getattr(settings, ...)`` calls and in
    NO field declaration, and ``Settings`` forbids extras — so it cannot be set
    at all. Its "fallback" is therefore the only path either function takes,
    which is why the round trip below is pinned on the fallback rather than on
    a configured host. If someone declares the field, this test turns red and
    the round trip needs a second arm.
    """
    from app.core.config import settings as real_settings

    assert "PUBLIC_API_BASE" not in type(real_settings).model_fields
    with pytest.raises((AttributeError, ValueError)):
        real_settings.PUBLIC_API_BASE = "https://api.example.test"


def test_what_the_daemon_branch_mints_is_what_this_classifier_accepts(monkeypatch):
    """The round trip that guards the DAEMON branch.

    ``canvas_generation._absolute_media_url`` absolutises a relative durable
    URL before handing it to the user's machine to fetch; this classifier
    decides whether an absolute URL is ours. If they disagree about the host, a
    reference we minted ourselves is classified ``unknown`` and reported as a
    dropped ref — for no reason a user could act on.

    This is the DEFAULT deployment (see the test above: ``PUBLIC_API_BASE``
    cannot be set), and it is the configuration that used to fail — the minter
    fell back to a literal the classifier had never heard of. The assertion is
    that the two AGREE, not that either equals a particular host, so moving the
    default breaks nothing here as long as both sides move together.
    """
    from app.workflows import canvas_generation as cg

    monkeypatch.setattr(gm_svc.settings, "MEDIA_PUBLIC_URL", "")

    absolute = cg._absolute_media_url("/api/v1/resources/91/cover")

    assert absolute.startswith("http")
    assert classify_reference_url(absolute) == ("resource", 91), (
        f"the daemon branch mints {absolute!r} and the classifier calls it "
        "foreign — a reference we made ourselves would be dropped"
    )
    # Negative control: the check is a host allowlist, not "any absolute URL".
    assert classify_reference_url(
        "https://cdn.evil.test/api/v1/resources/91/cover"
    ) == ("unknown", None)


def test_a_generated_media_url_on_our_own_host_round_trips_too(monkeypatch):
    """The other durable shape, through the same allowlist — so a fix that
    special-cased only the resources path is visible."""
    from app.workflows import canvas_generation as cg

    monkeypatch.setattr(gm_svc.settings, "MEDIA_PUBLIC_URL", "")

    absolute = cg._absolute_media_url("/api/v1/generated-media/5/file")

    assert classify_reference_url(absolute) == ("genmedia", 5)


# ── the resolver ───────────────────────────────────────────────────────────


class _FakeRelations:
    """Stands in for ``AssetRelationsRepository`` — the same two calls the
    real reference path makes (``resource_in_scope`` then
    ``resource_media_rows``), so a regression that skips either is visible."""

    def __init__(self, *, in_scope: bool = True, rows=None, log=None):
        self._in_scope = in_scope
        self._rows = rows if rows is not None else {}
        self.log = log if log is not None else []

    async def resource_in_scope(self, resource_id: int, scope_id: int) -> bool:
        self.log.append(("in_scope", int(resource_id), int(scope_id)))
        return self._in_scope

    async def resource_media_rows(self, resource_ids, *, system_reason: str):
        self.log.append(("rows", [int(r) for r in resource_ids], system_reason))
        return {int(k): v for k, v in self._rows.items()}


def _patch_relations(monkeypatch, fake):
    monkeypatch.setattr(relations_mod, "AssetRelationsRepository", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_in_scope_image_resource_yields_a_real_local_path(monkeypatch, tmp_path):
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "teams/9/resources/2026/09/02/abc/photo.png"
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(b"IMG")
    _patch_relations(
        monkeypatch,
        _FakeRelations(
            rows={91: {"id": 91, "file_path": rel, "mime_type": "image/png"}}
        ),
    )

    async with resource_local_path(
        "/api/v1/resources/91/cover", scope_id=SCOPE, media_kind="image"
    ) as out:
        assert out.reason is None
        assert out.path == str(abs_path.resolve())
        assert os.path.isfile(out.path)

    assert abs_path.exists(), "a filesystem row must never be cleaned up"


@pytest.mark.asyncio
async def test_a_video_resource_falls_back_to_its_derived_image(monkeypatch, tmp_path):
    """The SAME ladder ``AssetsService._materialize_references`` walks:
    original when the row is itself an image, else thumbnail, else cover. Two
    ladders that have to agree is how the preview and the run start showing
    different pictures."""
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path))
    thumb = tmp_path / "thumbs/91.jpg"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"THUMB")
    _patch_relations(
        monkeypatch,
        _FakeRelations(
            rows={
                91: {
                    "id": 91,
                    "file_path": "videos/91.mp4",
                    "mime_type": "video/mp4",
                    "thumbnail_path": "thumbs/91.jpg",
                }
            }
        ),
    )

    async with resource_local_path("/api/v1/resources/91/file", scope_id=SCOPE) as out:
        assert out.reason is None
        assert out.path == str(thumb.resolve())


@pytest.mark.asyncio
async def test_a_cross_scope_resource_is_refused_and_never_read(monkeypatch):
    """The scope test must run BEFORE the row read, and a failure must be
    reported as ``not_in_scope`` rather than collapsed into "no image"."""
    fake = _patch_relations(monkeypatch, _FakeRelations(in_scope=False))

    async with resource_local_path("/api/v1/resources/91/cover", scope_id=SCOPE) as out:
        assert out.path is None
        assert out.reason == "not_in_scope"

    assert [entry[0] for entry in fake.log] == ["in_scope"], (
        "the resources row was read for an out-of-scope id — the scope test "
        "has stopped gating the read"
    )


@pytest.mark.asyncio
async def test_a_remote_stored_value_is_refused_case_insensitively(monkeypatch):
    """``HTTPS://cdn…`` in ``file_path`` is somebody else's URL, not a path we
    can materialize. A bare ``startswith("http")`` gets this wrong in both
    directions, which is why the shared ``_is_stored_path`` is scheme-exact."""
    _patch_relations(
        monkeypatch,
        _FakeRelations(
            rows={
                91: {
                    "id": 91,
                    "file_path": "HTTPS://cdn.example/x.png",
                    "mime_type": "image/png",
                }
            }
        ),
    )
    async with resource_local_path("/api/v1/resources/91/cover", scope_id=SCOPE) as out:
        assert out.path is None
        assert out.reason == "no_image_file"


@pytest.mark.asyncio
async def test_a_row_with_no_image_at_all_reports_no_image_file(monkeypatch):
    _patch_relations(
        monkeypatch,
        _FakeRelations(
            rows={91: {"id": 91, "file_path": "docs/91.pdf", "mime_type": "app/pdf"}}
        ),
    )
    async with resource_local_path("/api/v1/resources/91/cover", scope_id=SCOPE) as out:
        assert out.reason == "no_image_file"


@pytest.mark.asyncio
async def test_a_missing_row_reports_rather_than_crashing(monkeypatch):
    _patch_relations(monkeypatch, _FakeRelations(rows={}))
    async with resource_local_path("/api/v1/resources/91/cover", scope_id=SCOPE) as out:
        assert out.reason == "no_image_file"


@pytest.mark.asyncio
async def test_materialize_failure_is_reported_not_raised(monkeypatch):
    """One unreadable reference must not fail the run: generating with two of
    three references is a worse picture, not a broken request."""
    _patch_relations(
        monkeypatch,
        _FakeRelations(
            rows={
                91: {
                    "id": 91,
                    "file_path": "sb://chat-media/t9/ab/cd/dead.png",
                    "mime_type": "image/png",
                }
            }
        ),
    )

    async def _boom(self, key, *, start=None, end=None, chunk_size=None):
        raise RuntimeError("storage-api down")
        yield b""  # pragma: no cover - generator shape only

    monkeypatch.setattr(ms.ObjectStore, "get_stream", _boom)

    async with resource_local_path("/api/v1/resources/91/cover", scope_id=SCOPE) as out:
        assert out.path is None
        assert out.reason == "materialize_failed"


@pytest.mark.asyncio
async def test_an_object_store_row_yields_a_temp_file_deleted_on_exit(monkeypatch):
    async def _fake_stream(self, key, *, start=None, end=None, chunk_size=None):
        yield b"object-store-bytes"

    monkeypatch.setattr(ms.ObjectStore, "get_stream", _fake_stream)
    _patch_relations(
        monkeypatch,
        _FakeRelations(
            rows={
                91: {
                    "id": 91,
                    "file_path": "sb://chat-media/t9/ab/cd/dead.png",
                    "mime_type": "image/png",
                }
            }
        ),
    )

    captured = None
    async with resource_local_path("/api/v1/resources/91/cover", scope_id=SCOPE) as out:
        captured = out.path
        assert captured is not None
        assert open(captured, "rb").read() == b"object-store-bytes"
    assert not os.path.exists(captured), "the temp file outlived its block"


@pytest.mark.asyncio
async def test_a_non_resource_url_reports_unknown_shape(monkeypatch):
    _patch_relations(monkeypatch, _FakeRelations())
    async with resource_local_path(
        "/api/v1/generated-media/5/cover", scope_id=SCOPE
    ) as out:
        assert out.reason == "unknown_shape"


@pytest.mark.asyncio
async def test_a_non_image_media_kind_is_reported_not_guessed(monkeypatch):
    """The ladder resolves IMAGE bytes only. Saying so is the point — a
    resource silently missing from a video run is the failure mode."""
    _patch_relations(monkeypatch, _FakeRelations())
    async with resource_local_path(
        "/api/v1/resources/91/cover", scope_id=SCOPE, media_kind="video"
    ) as out:
        assert out.reason == "no_image_file"


@pytest.mark.asyncio
async def test_the_audit_reason_travels_from_this_module(monkeypatch, tmp_path):
    """``resource_media_rows`` requires ``system_reason`` keyword-only so the
    audit line names whoever decided the cross-user read was legitimate.
    Filing the canvas workflow's read under the generate-slot justification
    would be a true-looking log line about the wrong access."""
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path))
    rel = "x.png"
    (tmp_path / rel).write_bytes(b"IMG")
    fake = _patch_relations(
        monkeypatch,
        _FakeRelations(
            rows={91: {"id": 91, "file_path": rel, "mime_type": "image/png"}}
        ),
    )
    async with resource_local_path("/api/v1/resources/91/cover", scope_id=SCOPE):
        pass
    rows_call = next(e for e in fake.log if e[0] == "rows")
    assert rows_call[2] == CANVAS_REFERENCE_READ_REASON
    assert CANVAS_REFERENCE_READ_REASON.startswith("canvas-generation:")


# ── the url-only variant the daemon branch uses ────────────────────────────


@pytest.mark.asyncio
async def test_reference_reason_is_none_when_the_url_is_servable(monkeypatch, tmp_path):
    monkeypatch.setattr(gm_svc.settings, "DOWNLOAD_PATH", str(tmp_path))
    (tmp_path / "x.png").write_bytes(b"IMG")
    _patch_relations(
        monkeypatch,
        _FakeRelations(
            rows={91: {"id": 91, "file_path": "x.png", "mime_type": "image/png"}}
        ),
    )
    assert (
        await resource_reference_reason("/api/v1/resources/91/cover", scope_id=SCOPE)
        is None
    )


@pytest.mark.asyncio
async def test_reference_reason_gates_the_daemon_on_scope(monkeypatch):
    """The daemon fetches the url from the USER's machine, so an out-of-scope
    resource must be refused BEFORE the url is handed over, not after."""
    _patch_relations(monkeypatch, _FakeRelations(in_scope=False))
    assert (
        await resource_reference_reason("/api/v1/resources/91/cover", scope_id=SCOPE)
        == "not_in_scope"
    )
