"""Per-slide reverse-prompting: path safety + the slide_prompts merge.

Two things here can silently destroy data, so both are pinned:

  1. ``resolve_slide_file`` is the ONLY traversal guard between a URL path
     segment and the filesystem — it is shared by the slide serve endpoint
     and the caption endpoint precisely so there is one implementation to
     get right.
  2. ``merge_slide_prompt_map`` writes a column that holds EVERY slide's
     prompt. A write that isn't a whole-map merge erases the rest of the
     album, which is exactly the failure ``SlidePromptStrip``'s header
     warns about on the frontend side.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories.resources_repository import merge_slide_prompt_map
from app.services.media.slide_paths import (
    InvalidSlideName,
    SlideNotFound,
    is_image_slide,
    resolve_slide_file,
    validate_slide_name,
)

_USER = "11111111-1111-1111-1111-111111111111"
_RID = "9000000000000000001"
_MID = "9000000000000000002"


# ─── slide name validation / traversal ──────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "..",
        "sub/dir.jpg",
        "sub\\dir.jpg",
        "/etc/passwd",
        "a\x00.jpg",
        "",
        "   ",
    ],
)
def test_validate_slide_name_rejects_non_segments(name: str) -> None:
    with pytest.raises(InvalidSlideName):
        validate_slide_name(name)


def test_validate_slide_name_accepts_a_plain_filename() -> None:
    assert validate_slide_name("  001.jpg  ") == "001.jpg"


def test_is_image_slide_excludes_video_slides() -> None:
    # Albums legitimately carry .mp4 slides (list_slides serves them as
    # type="video"); handing one to a vision model is not a caption.
    assert is_image_slide("001.JPG")
    assert is_image_slide("cover.webp")
    assert not is_image_slide("002.mp4")
    assert not is_image_slide("notes.txt")


def _with_base(tmp_path: Path):
    return patch(
        "app.services.media.slide_paths.Utils.get_download_base_path",
        return_value=str(tmp_path),
    )


def test_resolve_slide_file_prefers_the_slides_subdirectory(tmp_path: Path) -> None:
    album = tmp_path / "web/douyin/123"
    (album / "slides").mkdir(parents=True)
    (album / "slides" / "001.jpg").write_bytes(b"a")
    (album / "001.jpg").write_bytes(b"b")

    with _with_base(tmp_path):
        assert (
            resolve_slide_file("web/douyin/123", "001.jpg")
            == (album / "slides" / "001.jpg").resolve()
        )


def test_resolve_slide_file_falls_back_to_the_flat_layout(tmp_path: Path) -> None:
    # 3 of the 74 production albums store slides flat under download_path.
    album = tmp_path / "web/douyin/456"
    album.mkdir(parents=True)
    (album / "002.png").write_bytes(b"a")

    with _with_base(tmp_path):
        assert (
            resolve_slide_file("web/douyin/456", "002.png")
            == (album / "002.png").resolve()
        )


def test_resolve_slide_file_raises_when_missing(tmp_path: Path) -> None:
    (tmp_path / "web/douyin/789").mkdir(parents=True)
    with _with_base(tmp_path), pytest.raises(SlideNotFound):
        resolve_slide_file("web/douyin/789", "nope.jpg")


def test_resolve_slide_file_rejects_traversal_in_the_slide_name(tmp_path: Path) -> None:
    (tmp_path / "web/douyin/123/slides").mkdir(parents=True)
    (tmp_path / "secret.jpg").write_bytes(b"s")
    with _with_base(tmp_path), pytest.raises(InvalidSlideName):
        resolve_slide_file("web/douyin/123", "../../secret.jpg")


def test_resolve_slide_file_rejects_a_download_path_escaping_the_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "downloads"
    (root).mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "001.jpg").write_bytes(b"x")
    with _with_base(root), pytest.raises(InvalidSlideName):
        resolve_slide_file("../outside", "001.jpg")


def test_resolve_slide_file_rejects_a_symlink_pointing_outside(tmp_path: Path) -> None:
    # The string guard can't see through a symlink — the resolved-path check
    # is what catches this one.
    album = tmp_path / "web/douyin/123/slides"
    album.mkdir(parents=True)
    secret = tmp_path.parent / f"{tmp_path.name}-secret.jpg"
    secret.write_bytes(b"s")
    (album / "001.jpg").symlink_to(secret)

    with _with_base(tmp_path), pytest.raises(SlideNotFound):
        resolve_slide_file("web/douyin/123", "001.jpg")


# ─── slide_prompts merge ────────────────────────────────────────────────


def test_merge_keeps_every_other_slide() -> None:
    current = {
        "001.jpg": {"en": "first", "zh": "第一张"},
        "003.jpg": {"zh": "第三张"},
    }
    merged = merge_slide_prompt_map(current, "002.jpg", {"en": "second"})

    assert merged["001.jpg"] == {"en": "first", "zh": "第一张"}
    assert merged["003.jpg"] == {"zh": "第三张"}
    assert merged["002.jpg"] == {"en": "second"}
    # The input map is not mutated — the caller may still be holding it.
    assert "002.jpg" not in current


def test_merge_keeps_a_hand_written_negative_on_the_same_slide() -> None:
    # The caption contract has no negative side, so a regenerate must not
    # wipe the negative the user typed in the strip.
    current = {"001.jpg": {"en": "old", "neg_en": "no watermark", "neg_zh": "无水印"}}
    merged = merge_slide_prompt_map(current, "001.jpg", {"en": "new", "zh": "新的"})

    assert merged["001.jpg"] == {
        "en": "new",
        "zh": "新的",
        "neg_en": "no watermark",
        "neg_zh": "无水印",
    }


def test_merge_handles_a_null_column() -> None:
    assert merge_slide_prompt_map(None, "001.jpg", {"en": "x"}) == {
        "001.jpg": {"en": "x"}
    }


def test_merge_survives_a_corrupt_non_dict_entry() -> None:
    # A legacy/hand-edited row could hold a string where an object belongs;
    # that must not blow up the whole album's write.
    assert merge_slide_prompt_map({"001.jpg": "oops"}, "001.jpg", {"en": "x"}) == {
        "001.jpg": {"en": "x"}
    }


# ─── workflow ───────────────────────────────────────────────────────────


def _make_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.update_progress = AsyncMock(return_value=None)
    return mgr


@pytest.mark.asyncio
async def test_caption_slide_workflow_merges_only_this_slide(tmp_path: Path) -> None:
    from app.workflows import caption_slide as m

    album = tmp_path / "web/douyin/123/slides"
    album.mkdir(parents=True)
    (album / "002.jpg").write_bytes(b"jpeg")

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(
        return_value={"id": _RID, "media_id": _MID, "mime_type": "image/jpeg"}
    )
    repo.merge_slide_prompt = AsyncMock(return_value={"en": "a prompt"})

    media_repo = MagicMock()
    media_repo.get_by_id = AsyncMock(return_value={"download_path": "web/douyin/123"})

    call = AsyncMock(return_value={"en": "a prompt", "zh": "一句提示词"})

    with (
        _with_base(tmp_path),
        patch.object(
            m,
            "resolve_caption_provider",
            AsyncMock(
                return_value={
                    "provider_key": "qwen",
                    "provider_config": {},
                    "agent_model": "m",
                    "agent_slug": "caption",
                }
            ),
        ),
        patch.object(m, "call_caption", call),
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.repositories.media_repository.MediaRepository",
            return_value=media_repo,
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=_make_manager(),
        ),
    ):
        result = await inspect.unwrap(m.caption_slide_workflow)(
            resource_id=_RID, user_id=_USER, media_id=_MID, slide_name="002.jpg"
        )

    assert result["status"] == "ok"
    assert result["slide_name"] == "002.jpg"
    # The vision call got the resolved slide file, not the album directory.
    assert call.await_args.kwargs["abs_path"] == str((album / "002.jpg").resolve())
    # The write goes through the per-slide merge, never a whole-column PATCH.
    repo.merge_slide_prompt.assert_awaited_once_with(
        _RID, "002.jpg", {"en": "a prompt", "zh": "一句提示词"}
    )
    repo.update_resource.assert_not_called()


@pytest.mark.asyncio
async def test_caption_slide_workflow_writes_no_ai_tags(tmp_path: Path) -> None:
    # An album runs this once PER SLIDE; tagging each one would multiply the
    # shared tag vocabulary by the slide count. Pinned as a contract, not an
    # oversight.
    source = inspect.getsource(
        inspect.unwrap(
            __import__(
                "app.workflows.caption_slide", fromlist=["caption_slide_workflow"]
            ).caption_slide_workflow
        )
    )
    assert "write_ai_tags" not in source
