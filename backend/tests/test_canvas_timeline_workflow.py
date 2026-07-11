"""canvas_timeline workflow steps (G8 — timeline director, Phase 5).

Segment-chained video: each segment generates through the DB-catalog video
provider; segment i>0 is guided by segment i-1's TAIL FRAME (i2v chaining —
the LTX multi-segment semantics adapted to jimeng/Ark, decision ③). The
finished segments concat through ffmpeg into one film, registered through the
generated-media store. Failures raise (route C).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.workflows.canvas_timeline import (
    concat_segments_step,
    extract_tail_frame_step,
    generate_segment_step,
)


@pytest.mark.asyncio
async def test_segment_step_passes_duration_and_tail_guide():
    provider = SimpleNamespace(
        generate_video=AsyncMock(
            return_value=SimpleNamespace(local_path="/tmp/seg1.mp4", mime="video/mp4")
        )
    )
    with (
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
            new=AsyncMock(return_value=(provider, "jimeng-video-3")),
        ),
        patch(
            "app.services.library.generated_media_service.resolve_generated_media_local_path",
            new=AsyncMock(return_value="/tmp/tail0.png"),
        ),
    ):
        out = await generate_segment_step(
            prompt="waves crash",
            seconds=4,
            model="",
            aspect="16:9",
            guide_url="/api/v1/generated-media/7/cover",
        )

    call = provider.generate_video.await_args
    assert call.kwargs["prompt"] == "waves crash"
    assert call.kwargs["image_path"] == "/tmp/tail0.png"
    assert call.kwargs["aspect"] == "16:9"
    assert out["local_path"] == "/tmp/seg1.mp4"


@pytest.mark.asyncio
async def test_segment_step_first_segment_is_t2v():
    provider = SimpleNamespace(
        generate_video=AsyncMock(
            return_value=SimpleNamespace(local_path="/tmp/seg0.mp4", mime="video/mp4")
        )
    )
    with patch(
        "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
        new=AsyncMock(return_value=(provider, "jimeng-video-3")),
    ):
        await generate_segment_step(
            prompt="opening shot",
            seconds=5,
            model="",
            aspect="",
            guide_url=None,
        )
    assert provider.generate_video.await_args.kwargs["image_path"] is None


@pytest.mark.asyncio
async def test_segment_step_raises_without_file():
    provider = SimpleNamespace(
        generate_video=AsyncMock(return_value=SimpleNamespace(local_path=None))
    )
    with patch(
        "app.services.media.parsers.video_providers.db_registry.resolve_video_provider",
        new=AsyncMock(return_value=(provider, "m")),
    ):
        with pytest.raises(RuntimeError):
            await generate_segment_step(
                prompt="x", seconds=3, model="", aspect="", guide_url=None
            )


@pytest.mark.asyncio
async def test_tail_frame_step_runs_ffmpeg_and_registers(tmp_path):
    seg = tmp_path / "seg.mp4"
    seg.write_bytes(b"fake")

    async def fake_exec(*args, **kwargs):
        # Simulate ffmpeg writing the frame file (last positional arg).
        out_path = args[-1]
        open(out_path, "wb").write(b"png")
        proc = SimpleNamespace(
            communicate=AsyncMock(return_value=(b"", b"")), returncode=0
        )
        return proc

    with (
        patch("asyncio.create_subprocess_exec", new=fake_exec),
        patch(
            "app.workflows.canvas_timeline.register_generated_media",
            new=AsyncMock(return_value={"id": 42}),
        ),
        patch(
            "app.workflows.canvas_timeline._resolve_personal_team_id",
            new=AsyncMock(return_value="9"),
        ),
    ):
        url = await extract_tail_frame_step(
            segment_path=str(seg), user_id="u1", canvas_id=1, node_id="n1", index=0
        )
    assert url == "/api/v1/generated-media/42/cover"


@pytest.mark.asyncio
async def test_concat_step_builds_concat_command_and_returns_path(tmp_path):
    segs = []
    for i in range(3):
        p = tmp_path / f"seg{i}.mp4"
        p.write_bytes(b"fake")
        segs.append(str(p))

    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        out_path = args[-1]
        open(out_path, "wb").write(b"mp4")
        return SimpleNamespace(
            communicate=AsyncMock(return_value=(b"", b"")), returncode=0
        )

    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        out = await concat_segments_step(segment_paths=segs)

    assert out.endswith(".mp4")
    joined = " ".join(str(a) for a in captured["args"])
    assert "concat" in joined
    # All three inputs made it into the command.
    for p in segs:
        assert p in joined


@pytest.mark.asyncio
async def test_concat_step_single_segment_short_circuits(tmp_path):
    p = tmp_path / "only.mp4"
    p.write_bytes(b"fake")
    out = await concat_segments_step(segment_paths=[str(p)])
    assert out == str(p)


def test_progress_payload_carries_segment_frames_incrementally():
    """P2-1: tail frames reach task metadata as they are produced, so the
    frontend shows per-segment thumbnails mid-run and on failure — not only
    after a fully successful film."""
    from app.workflows.canvas_timeline import _progress_payload

    assert _progress_payload(1, 3) == {"segments_done": 1, "segments_total": 3}
    assert _progress_payload(2, 3, ["u1"]) == {
        "segments_done": 2,
        "segments_total": 3,
        "segment_frames": ["u1"],
    }
    # Empty frame lists stay out of the payload (no pointless jsonb churn).
    assert _progress_payload(1, 3, []) == {"segments_done": 1, "segments_total": 3}
