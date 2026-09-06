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
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.repositories.resources_repository import merge_slide_prompt_map
from app.services.media.slide_paths import (
    InvalidSlideName,
    SlideNotFound,
    album_key_prefix,
    is_image_slide,
    resolve_slide_file,
    resolve_slide_source,
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


# ─── object-store albums (resolve_slide_source) ─────────────────────────
#
# A migrated album's download_path is an sb:// prefix with nothing left on
# disk, so the filesystem-only resolver 404'd every per-slide operation on
# every album the storage migration had touched. Same two candidates, same
# order, probed with ObjectStore.exists instead of Path.is_file.


def _patch_exists(pred) -> object:
    return patch(
        "app.services.library.media_storage.ObjectStore.exists",
        new=AsyncMock(side_effect=pred),
    )


@pytest.mark.asyncio
async def test_resolve_slide_source_local_path_delegates_unchanged(
    tmp_path: Path,
) -> None:
    """本地 download_path 行为逐字不变:仍走 resolve_slide_file,返回绝对路径
    字符串(不是 sb:// 值),也不碰对象存储。"""
    album = tmp_path / "web/douyin/123/slides"
    album.mkdir(parents=True)
    (album / "001.jpg").write_bytes(b"a")

    exists = AsyncMock(side_effect=AssertionError("本地路径不该探对象存储"))
    with (
        _with_base(tmp_path),
        patch("app.services.library.media_storage.ObjectStore.exists", new=exists),
    ):
        got = await resolve_slide_source("web/douyin/123", "001.jpg")

    assert got == str((album / "001.jpg").resolve())


@pytest.mark.asyncio
async def test_resolve_slide_source_prefers_the_slides_key() -> None:
    probed: list[str] = []

    async def _exists(key: str) -> bool:
        probed.append(key)
        return key.endswith("slides/001.jpg")

    with _patch_exists(_exists):
        got = await resolve_slide_source("sb://library/t5/album/9/", "001.jpg")

    assert got == "sb://library/t5/album/9/slides/001.jpg"
    assert probed == ["t5/album/9/slides/001.jpg"]


@pytest.mark.asyncio
async def test_resolve_slide_source_falls_back_to_the_prefix_root() -> None:
    probed: list[str] = []

    async def _exists(key: str) -> bool:
        probed.append(key)
        return "slides/" not in key

    with _patch_exists(_exists):
        got = await resolve_slide_source("sb://library/t5/album/9/", "7613_0.jpg")

    assert got == "sb://library/t5/album/9/7613_0.jpg"
    assert probed == ["t5/album/9/slides/7613_0.jpg", "t5/album/9/7613_0.jpg"]


@pytest.mark.asyncio
async def test_resolve_slide_source_raises_slide_not_found() -> None:
    with _patch_exists(lambda key: False), pytest.raises(SlideNotFound):
        await resolve_slide_source("sb://library/t5/album/9/", "009.jpg")


@pytest.mark.asyncio
async def test_resolve_slide_source_rejects_traversal_before_probing() -> None:
    """名字里的 .. 在拼 key 之前就被挡掉 —— 对象键是平坦命名空间,但它会被
    插进 storage-api 的 URL path,HTTP 客户端会规范化 .. 并走出 bucket。"""
    exists = AsyncMock(side_effect=AssertionError("非法名字不该产生任何探测"))
    with patch("app.services.library.media_storage.ObjectStore.exists", new=exists):
        with pytest.raises(InvalidSlideName):
            await resolve_slide_source("sb://library/t5/album/9/", "../cover.jpg")


@pytest.mark.asyncio
async def test_resolve_slide_source_rejects_a_traversal_in_the_album_key() -> None:
    """损坏的 file_path 行(key 里带 ..)同样不能变成读原语。"""
    exists = AsyncMock(side_effect=AssertionError("非法前缀不该产生任何探测"))
    with patch("app.services.library.media_storage.ObjectStore.exists", new=exists):
        with pytest.raises(InvalidSlideName):
            await resolve_slide_source("sb://library/t5/../../etc/", "001.jpg")


def test_album_key_prefix_normalizes_slashes() -> None:
    assert album_key_prefix("t5/album/9/") == "t5/album/9/"
    # 少了尾斜杠 / 多了首斜杠都会让调用方的 startswith(prefix) 算错。
    assert album_key_prefix("/t5/album/9") == "t5/album/9/"


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
    repo.update_resource = AsyncMock(return_value=None)

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
    # The write goes through the per-slide merge, never a whole-column PATCH —
    # and mig 455's origin stamp rides along in that SAME call. A separate
    # PATCH would leave a window where the new text is persisted under the
    # previous writer's origin, and nothing downstream would say so.
    repo.merge_slide_prompt.assert_awaited_once_with(
        _RID,
        "002.jpg",
        {"en": "a prompt", "zh": "一句提示词"},
        extra={"prompt_origin": "captioned"},
    )
    repo.update_resource.assert_not_awaited()


@pytest.mark.asyncio
async def test_caption_slide_workflow_materializes_a_migrated_slide(
    tmp_path: Path,
) -> None:
    """已迁移图集:download_path 是 sb:// 前缀,slide 得先落临时盘再喂给
    vision(与 caption_asset 同一个 materialize 适配器),而不是把 sb:// 字符串
    当路径传下去 —— 后者在 provider 侧才炸,且 Task Center 里只看到一句
    File not found。"""
    from app.workflows import caption_slide as m

    local_copy = tmp_path / "materialized.jpg"
    local_copy.write_bytes(b"jpeg")

    @asynccontextmanager
    async def _fake_materialize(file_path: str):
        assert file_path == "sb://library/t5/album/9/slides/002.jpg"
        yield local_copy

    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(
        return_value={"id": _RID, "media_id": _MID, "mime_type": "image/jpeg"}
    )
    repo.merge_slide_prompt = AsyncMock(return_value={"en": "a prompt"})
    repo.update_resource = AsyncMock(return_value=None)

    media_repo = MagicMock()
    media_repo.get_by_id = AsyncMock(
        return_value={"download_path": "sb://library/t5/album/9/"}
    )

    call = AsyncMock(return_value={"en": "a prompt"})

    with (
        _patch_exists(lambda key: key.endswith("slides/002.jpg")),
        patch.object(m, "materialize", _fake_materialize),
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
    # vision 拿到的是临时盘上的真文件,不是 sb:// 字符串。
    assert call.await_args.kwargs["abs_path"] == str(local_copy)
    repo.merge_slide_prompt.assert_awaited_once_with(
        _RID, "002.jpg", {"en": "a prompt"}, extra={"prompt_origin": "captioned"}
    )


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
