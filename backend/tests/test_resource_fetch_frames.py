"""ResourceFetch mode='frames' — the agent must actually SEE the video.

Before this branch existed the tool answered ``mode='frames'`` with
"not yet implemented in v1", while ``prompt_composer`` had been
advertising ``frames`` to every model in the ``<available_resources>``
block — so a user asking "what's on screen?" got a refusal for a
capability the system message promised.

What is pinned here:

* the success shape is the IMAGE branch's shape (a list of
  ``image_url`` blocks with ``data:`` urls), because that is the only
  shape ``agent_runner._image_blocks`` promotes into a user message —
  a different-but-reasonable shape would return frames the vision model
  never sees, which is exactly the failure the image branch was fixed for;
* the file is located through the PR-B ladder, not ``resources.file_path``
  (empty by design for ``source_type='web'`` rows — over half the videos
  in production);
* per-frame extraction failures are REPORTED, never swallowed;
* ``args.frames`` is clamped, and the two budget constants stay tied to
  the modules they were copied from.
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.agent_framework.multimodal import Attachment, AttachmentKind
from app.services.ai.tools import resource_fetch_tool as rft
from app.services.ai.tools.resource_fetch_tool import resource_fetch
from app.services.media.render.video_frame_extractor import FrameExtractionResult

RID = "331000000000042"
UID = "b2180063-6860-4f97-9785-ad4eede16064"


# ── stubs ────────────────────────────────────────────────────────────


class _FakeMappingsResult:
    """``first()`` serves the access-check row; ``all()`` serves
    ``effective_ai_statuses``' in-flight task lookup (no active tasks —
    frames never consults the AI status columns anyway).

    ``first()`` projects the fixture through the columns the REAL
    statement selects (``columns``), rather than handing back the
    hand-written dict wholesale. That projection is the only thing
    connecting these tests to the SELECT itself: without it, dropping
    ``Resources.media_id`` from the query leaves every test green while
    production loses the PR-B ladder's second rung — i.e. loses frames
    for every ``source_type='web'`` video, which is most of them. Same
    family as CLAUDE.md's "边界 mock 必须用真实 JSON 形状": here the
    boundary mock's row SHAPE has to come from the real query.
    """

    def __init__(self, row, columns):
        self._row = row
        self._columns = columns

    def first(self):
        if self._row is None:
            return None
        missing = [c for c in self._columns if c not in self._row]
        assert not missing, (
            f"the access-check SELECT reads {missing}, which this fixture row "
            f"does not carry — add the column(s) to the fixture"
        )
        return {c: self._row[c] for c in self._columns}

    def all(self):
        return []


class _FakeResult:
    """Three query sites share this session and each uses a DIFFERENT
    accessor, so one result object can serve them all without a fragile
    call-order queue: access check → ``.mappings().first()``, AI status →
    ``.mappings().all()``, PR-B ladder → ``.scalar()``."""

    def __init__(self, row, ladder, columns):
        self._row = row
        self._ladder = ladder
        self._columns = columns

    def mappings(self):
        return _FakeMappingsResult(self._row, self._columns)

    def scalar(self):
        return self._ladder


class _StubSession:
    def __init__(self, row, ladder):
        self._row = row
        self._ladder = ladder

    async def execute(self, stmt):
        return _FakeResult(self._row, self._ladder, list(stmt.selected_columns.keys()))


def _patch_db(row: dict | None, *, ladder_download_path=None):
    """Patch the two DB boundaries _fetch_dispatch touches.

    ``ladder_download_path`` is what ``parsed_media.download_path``
    returns on the ladder's second rung (None = no row).
    """

    @asynccontextmanager
    async def _read_scope():
        yield _StubSession(row, ladder_download_path)

    @asynccontextmanager
    async def _system_request_scope(reason: str):
        yield None

    return (
        patch("app.db.session.read_scope", new=_read_scope),
        patch("app.db.scope.system_request_scope", new=_system_request_scope),
    )


def _video_row(**overrides):
    row = {
        "id": RID,
        "mime": "video/mp4",
        "name": "clip.mp4",
        "file_path": "personal/u1/clip.mp4",
        "media_id": None,
        "brief": None,
        "transcript_status": "none",
        "summary_status": "none",
    }
    row.update(overrides)
    return row


def _jpeg_attachment(ts: float, payload: bytes = b"\xff\xd8jpegbytes"):
    b64 = base64.b64encode(payload).decode("ascii")
    return Attachment(
        kind=AttachmentKind.VIDEO_THUMBNAIL,
        data_url=f"data:image/jpeg;base64,{b64}",
        mime="image/jpeg",
        alt_text=f"frame at {ts:.1f}s",
    )


class _FakePath:
    def __init__(self, p, exists):
        self._p = p
        self._exists = exists

    def exists(self):
        return self._exists

    def __str__(self):
        return self._p


def _patch_extraction(
    result, *, materialize_exists=True, spy=None, materialize_delay=0.0
):
    """Patch materialize + extract_frames at the lazy-import source
    modules (the tool imports them inside the function body).

    ``materialize_delay`` stalls INSIDE materialize — i.e. in the half
    that has no timeout of its own — so a test can prove the outer
    deadline covers the download and not just the ffmpeg run.
    """

    @asynccontextmanager
    async def _materialize(file_path):
        if spy is not None:
            spy["file_path"] = file_path
        if materialize_delay:
            import asyncio as _asyncio

            await _asyncio.sleep(materialize_delay)
        yield _FakePath(file_path, materialize_exists)

    async def _extract_frames(path, **kwargs):
        if spy is not None:
            spy.update(kwargs)
            spy["path"] = path
        if isinstance(result, Exception):
            raise result
        return result

    return (
        patch("app.services.library.media_storage.materialize", new=_materialize),
        patch(
            "app.services.media.render.video_frame_extractor.extract_frames",
            new=_extract_frames,
        ),
    )


async def _call(
    row,
    extraction,
    *,
    args=None,
    ladder_download_path=None,
    spy=None,
    materialize_exists=True,
    materialize_delay=0.0,
):
    read_scope_patch, system_scope_patch = _patch_db(
        row, ladder_download_path=ladder_download_path
    )
    mat_patch, extract_patch = _patch_extraction(
        extraction,
        materialize_exists=materialize_exists,
        spy=spy,
        materialize_delay=materialize_delay,
    )
    with read_scope_patch, system_scope_patch, mat_patch, extract_patch:
        return await resource_fetch(
            resource_id=RID,
            mode="frames",
            args=args,
            user_id=UID,
            available_refs={RID},
            request_cache={},
        )


# ── success shape ────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_frames_returns_image_url_blocks_like_the_image_branch():
    """N frames → N image_url blocks with data: urls.

    The shape check is the point: agent_runner._image_blocks only
    promotes ``content[i]["type"] == "image_url"`` blocks whose ``url``
    starts with data:/http. Anything else and the model never sees the
    pixels.
    """
    frames = FrameExtractionResult(
        attachments=[_jpeg_attachment(t) for t in (1.0, 3.0, 5.0)],
        duration_seconds=10.0,
        sampled_at_seconds=[1.0, 3.0, 5.0],
    )
    result = await _call(_video_row(), frames, args={"frames": 3})

    assert "error" not in result, result
    assert "warning" not in result, result
    blocks = result["content"]
    assert len(blocks) == 3
    assert {b["type"] for b in blocks} == {"image_url"}
    assert all(b["url"].startswith("data:image/jpeg;base64,") for b in blocks)
    assert [b["alt"] for b in blocks] == [
        "frame at 1.0s",
        "frame at 3.0s",
        "frame at 5.0s",
    ]
    assert result["meta"]["mode"] == "frames"
    assert result["meta"]["frames_returned"] == 3
    assert result["meta"]["sampled_at_seconds"] == [1.0, 3.0, 5.0]

    # The promotion the whole shape exists for, exercised end to end.
    from app.services.ai.runner.agent_runner import _image_blocks

    assert len(_image_blocks(result)) == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_frames_uses_the_prb_ladder_not_the_file_path_column():
    """source_type='web' row: resources.file_path empty, path on
    parsed_media.download_path. Reading the column alone would report
    "no video file" for the videos users most often ask about."""
    spy: dict = {}
    frames = FrameExtractionResult(
        attachments=[_jpeg_attachment(2.0)],
        duration_seconds=5.0,
        sampled_at_seconds=[2.0],
    )
    result = await _call(
        _video_row(file_path=None, media_id=771000000000001),
        frames,
        args={"frames": 1},
        ladder_download_path="sb://library/t42/ab/cd/abcd.mp4",
        spy=spy,
    )
    assert "error" not in result, result
    assert spy["file_path"] == "sb://library/t42/ab/cd/abcd.mp4"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_access_check_selects_media_id_so_the_ladder_can_use_it():
    """The SELECT must read media_id, not just the code that consumes it.

    The ladder's second rung keys off ``media_id``; drop that column from
    the query and ``resolve_resource_file_path`` silently sees ``None``
    and returns "no file" for every ``source_type='web'`` video — most of
    them in production. Nothing else in this suite reads the statement,
    so without this the column is deletable with the whole suite green.
    ``_FakeMappingsResult.first`` projects the fixture through the real
    statement's columns, which is what makes this falsifiable.
    """
    seen: dict = {}

    async def _spy_ladder(resource: dict):
        seen["row"] = dict(resource)
        return "personal/u1/from-ladder.mp4"

    read_scope_patch, system_scope_patch = _patch_db(
        _video_row(file_path=None, media_id=771000000000009)
    )
    mat_patch, extract_patch = _patch_extraction(
        FrameExtractionResult([_jpeg_attachment(1.0)], 4.0, [1.0])
    )
    with (
        read_scope_patch,
        system_scope_patch,
        mat_patch,
        extract_patch,
        patch(
            "app.services.library.resource_file_path.resolve_resource_file_path",
            new=_spy_ladder,
        ),
    ):
        result = await resource_fetch(
            resource_id=RID,
            mode="frames",
            args=None,
            user_id=UID,
            available_refs={RID},
            request_cache={},
        )

    assert "error" not in result, result
    assert "media_id" in seen["row"], (
        "the access-check SELECT no longer reads media_id — the PR-B "
        "ladder's second rung is dead and every source_type='web' video "
        "loses frames"
    )
    assert seen["row"]["media_id"] == 771000000000009


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outer_deadline_covers_the_download_not_just_ffmpeg(monkeypatch):
    """The 180 s bound must wrap materialize(), which has no timeout of
    its own — a multi-GB source video meeting a storage hiccup would
    otherwise hang the chat turn indefinitely.

    ``test_timeout_is_typed`` does NOT cover this: it injects a
    TimeoutError as extract_frames' result, which only proves the
    ``except TimeoutError`` arm exists. Here the stall is inside
    materialize and extract_frames must never be reached — that second
    assertion is the whole point, since a stall on the ffmpeg side would
    pass even with the deadline moved inside the download.
    """
    monkeypatch.setattr(rft, "FRAMES_TOTAL_DEADLINE_SECONDS", 0.05)
    spy: dict = {}
    result = await _call(
        _video_row(),
        FrameExtractionResult([_jpeg_attachment(1.0)], 4.0, [1.0]),
        spy=spy,
        materialize_delay=0.5,
    )
    assert "timed out" in result["error"], result
    assert "path" not in spy, "extract_frames ran despite the deadline expiring"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_frames_extraction_uses_default_width_and_bounded_timeout():
    spy: dict = {}
    frames = FrameExtractionResult(
        attachments=[_jpeg_attachment(1.0)],
        duration_seconds=4.0,
        sampled_at_seconds=[1.0],
    )
    await _call(_video_row(), frames, spy=spy)

    from app.services.media.render.video_frame_extractor import DEFAULT_FRAME_WIDTH

    assert spy["frame_width"] == DEFAULT_FRAME_WIDTH
    assert spy["timeout_seconds"] == rft.FRAMES_EXTRACT_TIMEOUT_SECONDS
    assert spy["num_frames"] == rft.FRAMES_DEFAULT_COUNT


# ── file not available → typed error, no guessing ────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_resolvable_path_is_a_typed_error():
    """Both ladder rungs empty (e.g. download still running) — say so,
    do not hand a None to ffmpeg."""
    result = await _call(
        _video_row(file_path=None, media_id=None),
        FrameExtractionResult([], None, []),
    )
    assert "content" not in result
    assert "video file not available for frame extraction" in result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_album_directory_prefix_is_not_fed_to_ffmpeg():
    """A photo album's download_path is a DIRECTORY. The ladder returns
    None for it; the branch must report that, not extract from a dir."""
    spy: dict = {}
    result = await _call(
        _video_row(file_path=None, media_id=771000000000002),
        FrameExtractionResult([], None, []),
        ladder_download_path="t42/album/771000000000002/",
        spy=spy,
    )
    assert "error" in result
    assert "photo album" in result["error"]
    assert spy == {}, "ffmpeg must not run on a directory prefix"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_materialized_file_missing_is_a_typed_error():
    result = await _call(
        _video_row(),
        FrameExtractionResult([], None, []),
        materialize_exists=False,
    )
    assert "not on disk" in result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extractor_error_is_surfaced_not_flattened():
    result = await _call(
        _video_row(),
        FrameExtractionResult([], None, [], error="ffmpeg not installed"),
    )
    assert result["error"] == "frame extraction failed: ffmpeg not installed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_storage_failure_names_the_class_instead_of_fetch_failed():
    """The entry point's broad except would flatten everything into
    "fetch failed: X"; the branch keeps the reason attached to frames."""
    result = await _call(_video_row(), RuntimeError("s3 timeout"))
    assert "video file not available for frame extraction" in result["error"]
    assert "RuntimeError" in result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_path_escape_is_rejected():
    result = await _call(_video_row(), ValueError("file_path escapes DOWNLOAD_PATH"))
    assert result["error"] == "video file path is outside the allowed directory"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_timeout_is_typed():
    result = await _call(_video_row(), TimeoutError())
    assert "timed out" in result["error"]


# ── per-frame failures must not be swallowed ─────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_extraction_reports_the_shortfall():
    """extract_frames degrades a failed frame to "one fewer attachment",
    which on its own is indistinguishable from a short video. The agent
    must be able to tell the user it is looking at 4 of 6."""
    frames = FrameExtractionResult(
        attachments=[_jpeg_attachment(t) for t in (1.0, 2.0, 3.0, 4.0)],
        duration_seconds=12.0,
        sampled_at_seconds=[1.0, 2.0, 3.0, 4.0],
    )
    result = await _call(_video_row(), frames, args={"frames": 6})

    assert len(result["content"]) == 4
    assert result["meta"]["frames_requested"] == 6
    assert result["meta"]["frames_returned"] == 4
    assert "2 of 6 frames could not be extracted" in result["warning"]
    # A shortfall is NOT an error — the 4 good frames must still promote.
    from app.services.ai.runner.agent_runner import _image_blocks

    assert len(_image_blocks(result)) == 4


@pytest.mark.unit
@pytest.mark.asyncio
async def test_oversize_frame_is_dropped_and_counted():
    huge = b"x" * (rft.FRAMES_MAX_BYTES_PER_FRAME + 1024)
    frames = FrameExtractionResult(
        attachments=[_jpeg_attachment(1.0), _jpeg_attachment(2.0, huge)],
        duration_seconds=6.0,
        sampled_at_seconds=[1.0, 2.0],
    )
    result = await _call(_video_row(), frames, args={"frames": 2})

    assert len(result["content"]) == 1
    assert "1 of 2 frames could not be extracted" in result["warning"]
    assert "exceeded the per-frame size budget" in result["warning"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_every_frame_oversize_says_so_instead_of_no_usable_frames():
    """ "ffmpeg produced nothing" and "ffmpeg produced frames we refused to
    inline" need different answers from the user (retry vs. the video is
    pathological), so they must not collapse into one message."""
    huge = b"x" * (rft.FRAMES_MAX_BYTES_PER_FRAME + 1024)
    frames = FrameExtractionResult(
        attachments=[_jpeg_attachment(1.0, huge), _jpeg_attachment(2.0, huge)],
        duration_seconds=6.0,
        sampled_at_seconds=[1.0, 2.0],
    )
    result = await _call(_video_row(), frames, args={"frames": 2})
    assert "content" not in result
    assert "exceeded the per-frame size budget" in result["error"]
    assert "no usable frames" not in result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_zero_usable_frames_is_an_error_not_an_empty_success():
    """An empty content list would promote nothing and read to the model
    as "I looked and there was nothing" — a wrong answer, not a failure."""
    result = await _call(
        _video_row(),
        FrameExtractionResult([], 9.0, []),
    )
    assert "content" not in result
    assert "no usable frames" in result["error"]


# ── frames=N handling ────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested,expected",
    [
        (None, rft.FRAMES_DEFAULT_COUNT),
        (1, 1),
        (12, 12),
        (40, rft.FRAMES_MAX_COUNT),
        (0, 1),
        (-3, 1),
        ("8", 8),
        (8.0, 8),
    ],
)
async def test_frames_arg_is_clamped(requested, expected):
    spy: dict = {}
    args = None if requested is None else {"frames": requested}
    await _call(
        _video_row(),
        FrameExtractionResult([_jpeg_attachment(1.0)], 4.0, [1.0]),
        args=args,
        spy=spy,
    )
    assert spy["num_frames"] == expected


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("bad_args", ["frames=6", ["frames"], 6])
async def test_non_object_args_is_typed_not_an_attributeerror(bad_args):
    """The tool schema declares args as an object but nothing enforces
    it, and models violate that regularly. Before frames, ``args`` was
    never dereferenced (it only fed the cache key's json.dumps), so a
    non-dict was harmless; dereferencing it turns the same input into
    ``fetch failed: AttributeError`` from the entry point's broad except
    plus an ERROR in the log — opaque to the model, which then has no
    idea what to fix."""
    spy: dict = {}
    result = await _call(
        _video_row(),
        FrameExtractionResult([_jpeg_attachment(1.0)], 4.0, [1.0]),
        args=bad_args,
        spy=spy,
    )
    assert "AttributeError" not in result.get("error", "")
    assert "args must be an object" in result["error"]
    assert spy == {}, "no extraction may run on a rejected argument"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["many", 2.5, True, [], {"n": 1}])
async def test_non_numeric_frames_arg_is_rejected_not_defaulted(bad):
    """Silently substituting the default would answer a question the
    model did not ask, with no way for it to notice."""
    spy: dict = {}
    result = await _call(
        _video_row(),
        FrameExtractionResult([_jpeg_attachment(1.0)], 4.0, [1.0]),
        args={"frames": bad},
        spy=spy,
    )
    assert "frames must be a whole number" in result["error"]
    assert spy == {}, "no extraction may run on a rejected argument"


# ── neighbouring modes are untouched ─────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_frames_on_audio_points_at_transcript():
    result = await _call(
        _video_row(mime="audio/mpeg", name="voice.mp3"),
        FrameExtractionResult([], None, []),
    )
    assert "only available for video" in result["error"]
    assert "transcript" in result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_frames_on_an_image_returns_the_image_itself():
    """The image IS the frame — answering the question beats a typed
    "wrong mode" that costs another round trip."""
    png = b"\x89PNG\r\n\x1a\n" + b"z" * 16
    row = {
        "id": RID,
        "mime": "image/png",
        "name": "shot.png",
        "file_path": "sb://library/t42/ab/cd/abcd.png",
        "media_id": None,
        "brief": None,
        "transcript_status": "none",
        "summary_status": "none",
    }

    async def _fake_get_stream(self, key, **kwargs):
        yield png

    from app.services.library.media_storage import ObjectStore

    read_scope_patch, system_scope_patch = _patch_db(row)
    with (
        read_scope_patch,
        system_scope_patch,
        patch.object(ObjectStore, "get_stream", _fake_get_stream),
    ):
        result = await resource_fetch(
            resource_id=RID,
            mode="frames",
            args=None,
            user_id=UID,
            available_refs={RID},
            request_cache={},
        )
    assert result["content"][0]["type"] == "image_url"
    assert result["meta"]["kind"] == "image"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_video_mode_still_errors():
    result = await _call(_video_row(), FrameExtractionResult([], None, []))
    read_scope_patch, system_scope_patch = _patch_db(_video_row())
    with read_scope_patch, system_scope_patch:
        other = await resource_fetch(
            resource_id=RID,
            mode="thumbnails",
            args=None,
            user_id=UID,
            available_refs={RID},
            request_cache={},
        )
    assert "unknown mode" in other["error"]
    assert result  # frames path itself unaffected


# ── budget constants stay tied to their sources ──────────────────────


@pytest.mark.unit
def test_frame_budget_constants_match_their_sources():
    """The two counts are copied, not imported (cover_frames drags in PIL
    plus the canvas graph, and this tool is on the chat hot path). This
    test is what makes that copy falsifiable."""
    from app.services.ai.chat.chat_attachment_resolver import (
        MAX_VIDEO_FRAMES_PER_ATTACHMENT,
    )
    from app.services.distribution.cover_frames import MAX_COVER_FRAMES

    assert rft.FRAMES_DEFAULT_COUNT == MAX_VIDEO_FRAMES_PER_ATTACHMENT
    assert rft.FRAMES_MAX_COUNT == MAX_COVER_FRAMES


@pytest.mark.unit
def test_the_prompt_advertises_the_counts_the_tool_actually_enforces():
    """The system message hardcodes "default 6, max 12". FRAMES_MAX_COUNT
    is pinned to MAX_COVER_FRAMES, so moving that constant turns the tool
    test red and gets the constant fixed — while this sentence would go
    on quietly promising 12 and the model would keep getting clamped to
    something else. Advertising a number the tool does not honour is the
    smaller version of the bug this whole task existed to fix."""
    from app.services.ai.prompts.prompt_composer import render_available_resources

    block = render_available_resources(
        [
            {
                "id": "1",
                "name": "clip.mp4",
                "kind": "video",
                "mime": "video/mp4",
                "size": 18_000_000,
                "scope": "personal",
                "updated_at": "2026-08-01T00:00:00Z",
                "brief": None,
                "transcript_status": "none",
                "summary_status": "none",
            }
        ]
    )
    assert f"default {rft.FRAMES_DEFAULT_COUNT}" in block
    assert f"max {rft.FRAMES_MAX_COUNT}" in block


@pytest.mark.unit
def test_frames_worst_case_payload_stays_bounded():
    """Budget stated as an assertion so a bump to either constant has to
    face the number it produces."""
    worst_raw = rft.FRAMES_MAX_COUNT * rft.FRAMES_MAX_BYTES_PER_FRAME
    worst_b64 = worst_raw * 4 / 3
    assert worst_raw <= 2.5 * 1024 * 1024
    assert worst_b64 <= 3.5 * 1024 * 1024
