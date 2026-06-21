"""Tests: canvas image/video generation capture into Tier-1 generated_media.

Verifies that _run_image_gen and _run_video_gen call register_generated_media
after a successful generation (best-effort: failures in the capture path must
not affect the canvas run result).
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_image_storyboard_svc():
    """Return a fake storyboard service whose generate_image is async."""

    async def _generate_image(**kwargs):
        return {"image_url": "http://x/y.png"}

    class _Svc:
        generate_image = staticmethod(_generate_image)

    return _Svc()


def _make_video_storyboard_svc():
    """Return a fake storyboard service whose generate_video is async."""

    async def _generate_video(**kwargs):
        return {"video_url": "http://x/v.mp4"}

    class _Svc:
        generate_video = staticmethod(_generate_video)

    return _Svc()


# ---------------------------------------------------------------------------
# image_gen capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_gen_captures_generated_media(monkeypatch):
    """Successful image_gen triggers register_generated_media with correct args."""
    import app.services.canvas.canvas_run_service as crs

    calls: dict = {}

    async def _fake_register(**kwargs):
        calls.update(kwargs)
        return {"id": 1}

    async def _fake_scope(uid):
        return "42"

    monkeypatch.setattr(crs, "register_generated_media", _fake_register)
    monkeypatch.setattr(crs, "_resolve_personal_team_id", _fake_scope)

    svc = crs.CanvasRunService()
    monkeypatch.setattr(
        svc, "_storyboard_ai_service", lambda: _make_image_storyboard_svc()
    )

    res = await svc._run_image_gen(
        node={"data": {"prompt": "cat"}},
        body="cat",
        node_id="n1",
        project_id="7",
        user_id="u-uuid",
        canvas_id=99,
    )

    assert res.ok, f"expected ok=True, got error={res.error!r}"
    assert calls.get("source_url") == "http://x/y.png", f"source_url mismatch: {calls}"
    origin = calls.get("origin")
    assert origin is not None, "register_generated_media was not called"
    assert origin.kind == "canvas_run"
    assert origin.canvas_id == 99
    assert origin.derivation_kind == "image_gen"
    assert calls.get("mime") == "image/png"
    assert calls.get("user_id") == "u-uuid"
    assert calls.get("scope_id") == 42  # int("42")


@pytest.mark.asyncio
async def test_image_gen_capture_failure_is_nonfatal(monkeypatch):
    """When register_generated_media raises, _run_image_gen still returns ok."""
    import app.services.canvas.canvas_run_service as crs

    async def _raise_register(**kwargs):
        raise RuntimeError("db down")

    async def _fake_scope(uid):
        return "42"

    monkeypatch.setattr(crs, "register_generated_media", _raise_register)
    monkeypatch.setattr(crs, "_resolve_personal_team_id", _fake_scope)

    svc = crs.CanvasRunService()
    monkeypatch.setattr(
        svc, "_storyboard_ai_service", lambda: _make_image_storyboard_svc()
    )

    res = await svc._run_image_gen(
        node={"data": {"prompt": "cat"}},
        body="cat",
        node_id="n1",
        project_id="7",
        user_id="u-uuid",
        canvas_id=99,
    )

    # Capture failure must NOT propagate — result is still ok.
    assert res.ok, f"expected ok=True even on capture error, got error={res.error!r}"
    assert res.text == "http://x/y.png"


# ---------------------------------------------------------------------------
# video_gen capture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_gen_captures_generated_media(monkeypatch):
    """Successful video_gen triggers register_generated_media with video args."""
    import app.services.canvas.canvas_run_service as crs

    calls: dict = {}

    async def _fake_register(**kwargs):
        calls.update(kwargs)
        return {"id": 2}

    async def _fake_scope(uid):
        return "42"

    monkeypatch.setattr(crs, "register_generated_media", _fake_register)
    monkeypatch.setattr(crs, "_resolve_personal_team_id", _fake_scope)

    svc = crs.CanvasRunService()
    monkeypatch.setattr(
        svc, "_storyboard_ai_service", lambda: _make_video_storyboard_svc()
    )

    res = await svc._run_video_gen(
        node={"data": {"source_image_url": "http://x/img.png", "prompt": "zoom"}},
        node_id="n2",
        project_id="7",
        user_id="u-uuid",
        canvas_id=77,
    )

    assert res.ok, f"expected ok=True, got error={res.error!r}"
    assert calls.get("source_url") == "http://x/v.mp4"
    origin = calls.get("origin")
    assert origin is not None
    assert origin.kind == "canvas_run"
    assert origin.canvas_id == 77
    assert origin.derivation_kind == "video_gen"
    assert calls.get("mime") == "video/mp4"
