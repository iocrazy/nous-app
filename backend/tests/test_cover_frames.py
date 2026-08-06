"""Distribution 封面抽帧 —— service 层测试。

四组，对应实现里四条各自能独立坏掉的性质：

1. **裁切几何**（``center_crop_region``）—— 纯函数，比例必须精确，极端源不能
   算出零/负尺寸的区域。
2. **编排**（``extract_cover_candidates`` / ``derive_cover_pair``）—— 每帧落一
   行、时间戳对齐、参数钳制、坏帧跳过。
3. **优雅降级** —— ``extract_frames`` 的契约是"失败返回空 attachments +
   error 而不抛"，封面这一层必须把那种软失败翻译成**类型化**的
   ``CoverFrameError``（带 status_code），而不是让它冒成 500。
4. **临时文件（§7.6）** —— 实现的模块 docstring 说得很明确：本模块不创建临时
   文件，唯一要保证的是"异常/超时路径也能走到那三个 ``finally``"。所以这里测
   的正是那些路径：抽帧抛异常、总超时到点、ffmpeg 失败、落库写失败。成功路径
   谁都会删干净；真实世界里失败远比成功常见，残留只会在失败路径上攒。

ffmpeg 与对象存储全程打桩 —— 这些测试不需要真二进制也不碰网络。
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.agent_framework.multimodal import Attachment, AttachmentKind
from app.services.canvas.image_crop import CropRegion, crop_normalized
from app.services.distribution import cover_frames as cf
from app.services.media.render.video_frame_extractor import FrameExtractionResult

pytestmark = pytest.mark.unit

# 走对象存储形状的 file_path —— materialize 的 sb:// 分支是唯一会创建临时文件
# 的那条，第 6 组的残留断言全靠它。
SB_VIDEO_PATH = "sb://library/t1/ab/cd/abcdef.mp4"


# ============================================================
# Helpers
# ============================================================


def _jpeg_bytes(w: int = 1080, h: int = 1920) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (20, 120, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _frame(image_bytes: bytes, ts: float | None = None) -> Attachment:
    return Attachment(
        kind=AttachmentKind.VIDEO_THUMBNAIL,
        data_url="data:image/jpeg;base64,"
        + base64.b64encode(image_bytes).decode("ascii"),
        mime="image/jpeg",
        alt_text=f"frame at {ts}s" if ts is not None else "frame",
    )


def _result(
    attachments: List[Attachment],
    *,
    sampled_at: Optional[List[float]] = None,
    duration: Optional[float] = 30.0,
    error: Optional[str] = None,
) -> FrameExtractionResult:
    return FrameExtractionResult(
        attachments=attachments,
        duration_seconds=duration,
        sampled_at_seconds=sampled_at if sampled_at is not None else [],
        error=error,
    )


class FakeRepo:
    """In-memory ``ResourcesRepository`` stand-in (same shape as the one in
    ``test_crop_derive_service``, extended to mint MANY rows — one cover run
    persists a row per frame)."""

    def __init__(
        self,
        *,
        source: Optional[Dict[str, Any]] = None,
        item: Optional[Dict[str, Any]] = None,
    ):
        self.source = source
        self.item = item
        self.created: List[Dict[str, Any]] = []
        self.updated: List[Dict[str, Any]] = []
        self.versions: List[Dict[str, Any]] = []
        self.items: List[Dict[str, Any]] = []

    async def get_resource_by_id(self, resource_id: str):
        if self.source and str(self.source.get("id")) == str(resource_id):
            return self.source
        return None

    async def get_first_resource_item(self, resource_id: str):
        return self.item

    async def create_resource(self, data: Dict[str, Any]):
        row = dict(data)
        row["id"] = f"99990000000000{len(self.created):02d}"
        self.created.append(row)
        return row

    async def update_resource(self, resource_id: str, data: Dict[str, Any]):
        base = next(r for r in self.created if str(r["id"]) == str(resource_id))
        row = {**base, **data}
        self.updated.append(row)
        return row

    async def create_version(self, data: Dict[str, Any]):
        self.versions.append(dict(data))
        return data

    async def create_resource_item(self, data: Dict[str, Any]):
        self.items.append(dict(data))
        return data


def _video_repo(**overrides) -> FakeRepo:
    source = {
        "id": "500",
        "file_path": SB_VIDEO_PATH,
        "file_type": "video",
        "mime_type": "video/mp4",
        "filename": "clip.mp4",
    }
    source.update(overrides)
    return FakeRepo(
        source=source,
        item={"scope_id": "scope-1", "folder_id": "folder-9", "library_id": "lib-3"},
    )


@pytest.fixture
def local_download_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every filesystem write at a tmp dir and keep the unified-storage
    write track off, so persistence is a pure local-disk operation (no DB, no
    object store)."""
    from app.core.config import settings
    from app.services.canvas import derive_persistence

    root = tmp_path / "downloads"
    root.mkdir()
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(root))
    # materialize 的读缓存关掉：开着的话 sb:// 源落在缓存目录而不是临时文件，
    # 那是**有意不删**的（LRU），会让第 6 组的残留断言测错东西。
    monkeypatch.setattr(settings, "MEDIA_S3_CACHE_DIR", "")

    async def _off() -> bool:
        return False

    monkeypatch.setattr(derive_persistence, "unified_storage_enabled", _off)
    return root


@pytest.fixture
def isolated_tmpdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the process-wide temp dir so residue is countable.

    ``materialize`` (``tempfile.mkstemp``) and ``extract_frames``
    (``tempfile.TemporaryDirectory``) both create their scratch space in the
    default temp dir. Pointing that at an empty dir turns "did the finally
    run?" into a directory listing.
    """
    scratch = tmp_path / "systmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    return scratch


class _FakeObjectStore:
    """Streams fixed bytes for any key — enough for ``materialize`` to take its
    ``sb://`` branch (the one that actually creates a temp file)."""

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    async def get_stream(self, key: str):
        yield b"\x00\x00\x00\x14ftypisom" + b"video-bytes" * 64


@pytest.fixture
def object_store(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.library import media_storage

    monkeypatch.setattr(media_storage, "ObjectStore", _FakeObjectStore)


def _stub_extractor(monkeypatch: pytest.MonkeyPatch, fake_extract) -> None:
    monkeypatch.setattr(cf, "extract_frames", fake_extract)


# ============================================================
# 1. 裁切几何 —— center_crop_region
# ============================================================


def test_vertical_region_keeps_full_width_of_a_9x16_source() -> None:
    """9:16 竖屏（短视频主流源）裁 3:4 只该切上下，宽度一像素不动。"""
    region = cf.center_crop_region(1080, 1920, cf.COVER_VERTICAL_ASPECT)
    assert region == CropRegion(x=0.0, y=0.125, width=1.0, height=0.75)


def test_horizontal_region_of_a_9x16_source_is_vertically_centred() -> None:
    region = cf.center_crop_region(1080, 1920, cf.COVER_HORIZONTAL_ASPECT)
    assert region.x == 0.0
    assert region.width == 1.0
    assert region.height == pytest.approx(0.421875)
    assert region.y == pytest.approx((1.0 - region.height) / 2.0)


def test_wide_source_is_cropped_on_the_sides_and_stays_centred() -> None:
    region = cf.center_crop_region(1920, 1080, cf.COVER_VERTICAL_ASPECT)
    assert region.y == 0.0
    assert region.height == 1.0
    assert region.x == pytest.approx((1.0 - region.width) / 2.0)


def test_source_already_at_target_aspect_is_a_full_frame_crop() -> None:
    """1080×1440 就是抖音竖版封面尺寸 —— 不该再裁掉任何东西。"""
    region = cf.center_crop_region(1080, 1440, cf.COVER_VERTICAL_ASPECT)
    assert region == CropRegion(x=0.0, y=0.0, width=1.0, height=1.0)


@pytest.mark.parametrize(
    "width,height",
    [
        (1080, 1920),
        (1920, 1080),
        (1000, 1000),
        (20000, 10),  # 超宽
        (10, 20000),  # 超高
        (1, 4000),
        (4000, 1),
    ],
)
@pytest.mark.parametrize(
    "aspect", [cf.COVER_VERTICAL_ASPECT, cf.COVER_HORIZONTAL_ASPECT]
)
def test_region_never_leaves_the_unit_square_for_extreme_aspect_ratios(
    width: int, height: int, aspect: float
) -> None:
    """极端长宽比不能算出负数 / 零尺寸 / 越界的归一化区域。

    ``crop_normalized`` 会拒绝越界区域，所以几何算错在下游表现为一个说不清
    原因的 422；这条测试把它钉在源头。
    """
    region = cf.center_crop_region(width, height, aspect)
    assert region.width > 0 and region.height > 0
    assert region.x >= 0.0 and region.y >= 0.0
    assert region.x + region.width <= 1.0 + 1e-9
    assert region.y + region.height <= 1.0 + 1e-9


@pytest.mark.parametrize("size", [(1080, 1920), (1920, 1080), (1000, 1000)])
@pytest.mark.parametrize(
    "aspect", [cf.COVER_VERTICAL_ASPECT, cf.COVER_HORIZONTAL_ASPECT]
)
def test_cropped_output_matches_the_requested_aspect(size, aspect: float) -> None:
    """比例是这个特性的全部意义 —— 比例不对平台会自己再裁一刀。"""
    w, h = size
    region = cf.center_crop_region(w, h, aspect)
    out = Image.open(
        BytesIO(crop_normalized(_jpeg_bytes(w, h), region, mime_type="image/jpeg"))
    )
    assert out.size[0] / out.size[1] == pytest.approx(aspect, abs=0.01)


@pytest.mark.parametrize("width,height", [(0, 100), (100, 0), (-5, 100)])
def test_non_positive_source_dimensions_raise_400(width: int, height: int) -> None:
    with pytest.raises(cf.CoverFrameError) as exc:
        cf.center_crop_region(width, height, cf.COVER_VERTICAL_ASPECT)
    assert exc.value.status_code == 400


def test_non_positive_target_aspect_raises_400() -> None:
    with pytest.raises(cf.CoverFrameError) as exc:
        cf.center_crop_region(100, 100, 0.0)
    assert exc.value.status_code == 400


# ============================================================
# 2. 源视频校验 —— load_source_video
# ============================================================


async def test_missing_source_resource_is_404() -> None:
    repo = FakeRepo(source=None, item=None)
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.load_source_video(repo, "nope")
    assert exc.value.status_code == 404


async def test_image_source_is_rejected_as_400() -> None:
    """封面抽帧只对视频有意义；把图片喂进来该是 400，而不是等 ffmpeg 去发现。"""
    repo = _video_repo(file_type="image", mime_type="image/png")
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.load_source_video(repo, "500")
    assert exc.value.status_code == 400


async def test_video_detected_by_mime_when_file_type_is_unset() -> None:
    repo = _video_repo(file_type=None, mime_type="video/quicktime")
    source = await cf.load_source_video(repo, "500")
    assert source.scope_id == "scope-1"
    assert source.folder_id == "folder-9"


async def test_source_without_file_path_is_400() -> None:
    repo = _video_repo(file_path=None)
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.load_source_video(repo, "500")
    assert exc.value.status_code == 400


async def test_source_without_scope_link_is_400() -> None:
    """没有 scope 归属就不知道候选帧该落到谁的库里 —— 不能瞎猜一个。"""
    repo = _video_repo()
    repo.item = None
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.load_source_video(repo, "500")
    assert exc.value.status_code == 400


# ============================================================
# 3. 抽帧编排 —— extract_cover_candidates
# ============================================================


async def test_every_frame_becomes_a_resource_row_carrying_its_timestamp(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _video_repo()
    frames = [_jpeg_bytes(1080, 1920) for _ in range(3)]

    async def fake_extract(path, **kw):
        return _result(
            [_frame(b, ts) for b, ts in zip(frames, (1.5, 15.0, 28.5))],
            sampled_at=[1.5, 15.0, 28.5],
        )

    _stub_extractor(monkeypatch, fake_extract)

    out = await cf.extract_cover_candidates(
        source_resource_id="500", user_id="user-A", num_frames=3, repo=repo
    )

    assert out.source_resource_id == "500"
    assert out.duration_seconds == 30.0
    assert [c.timestamp_seconds for c in out.candidates] == [1.5, 15.0, 28.5]
    assert all(c.width == 1080 and c.height == 1920 for c in out.candidates)
    # 每帧一行 resources，落在源视频同一个 scope / folder / library 下。
    assert len(repo.created) == 3
    assert all(r["source_type"] == "derived" for r in repo.created)
    assert all(i["scope_id"] == "scope-1" for i in repo.items)
    assert all(i["folder_id"] == "folder-9" for i in repo.items)
    assert all(i["library_id"] == "lib-3" for i in repo.items)
    # creator 是发起操作的人，不是源视频的作者。
    assert all(r["creator_id"] == "user-A" for r in repo.created)


async def test_fps_fallback_without_timestamps_yields_null_not_index_error(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffprobe 探不到时长时 ``extract_frames`` 只产帧、不产时间戳。

    按下标硬取 ``sampled_at_seconds`` 会 IndexError，把"帧其实是好的"变成一次
    500。候选的时间戳可空正是为此。
    """
    repo = _video_repo()

    async def fake_extract(path, **kw):
        return _result(
            [_frame(_jpeg_bytes(640, 360)) for _ in range(2)],
            sampled_at=[],
            duration=None,
        )

    _stub_extractor(monkeypatch, fake_extract)

    out = await cf.extract_cover_candidates(
        source_resource_id="500", user_id="u", num_frames=2, repo=repo
    )
    assert out.duration_seconds is None
    assert [c.timestamp_seconds for c in out.candidates] == [None, None]
    # 时间戳缺席时文件名退回序号，仍然互不重名。
    assert len({c.filename for c in out.candidates}) == 2


async def test_one_undecodable_frame_is_skipped_and_the_rest_still_persist(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一帧坏掉不该毁掉整次抽帧 —— 用户挑封面只需要一张好的。"""
    repo = _video_repo()
    good = _jpeg_bytes(1080, 1920)
    broken = Attachment(
        kind=AttachmentKind.VIDEO_THUMBNAIL,
        data_url="data:image/jpeg;base64,!!!not-base64!!!",
        mime="image/jpeg",
    )

    async def fake_extract(path, **kw):
        return _result(
            [_frame(good, 1.0), broken, _frame(good, 3.0)], sampled_at=[1.0, 2.0, 3.0]
        )

    _stub_extractor(monkeypatch, fake_extract)

    out = await cf.extract_cover_candidates(
        source_resource_id="500", user_id="u", num_frames=3, repo=repo
    )
    assert len(out.candidates) == 2
    # 跳过的那一帧不会让后面的时间戳错位。
    assert [c.timestamp_seconds for c in out.candidates] == [1.0, 3.0]


async def test_all_frames_undecodable_fails_422_rather_than_succeeding_empty(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """零候选是失败，不是"成功但空"。

    后者会让任务中心显示绿色的完成态而用户面前一张封面都没有 —— 与路线 C
    纪律 4（"失败必须 raise，不能 return failed dict"）同族的坑。
    """
    repo = _video_repo()
    broken = Attachment(
        kind=AttachmentKind.VIDEO_THUMBNAIL, data_url="", mime="image/jpeg"
    )

    async def fake_extract(path, **kw):
        return _result([broken, broken], sampled_at=[1.0, 2.0])

    _stub_extractor(monkeypatch, fake_extract)

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.extract_cover_candidates(
            source_resource_id="500", user_id="u", num_frames=2, repo=repo
        )
    assert exc.value.status_code == 422
    assert repo.created == []


@pytest.mark.parametrize(
    "requested,expected", [(0, 1), (-3, 1), (1, 1), (6, 6), (99, cf.MAX_COVER_FRAMES)]
)
async def test_frame_count_is_clamped_before_it_reaches_the_extractor(
    local_download_root: Path,
    object_store: None,
    monkeypatch: pytest.MonkeyPatch,
    requested: int,
    expected: int,
) -> None:
    """每帧都会在用户素材库里多留一行，所以封面这层的上限比 extractor 自己的
    32 更严。钳制失效 = 一次点击往库里灌 99 张图。"""
    repo = _video_repo()
    seen: Dict[str, Any] = {}

    async def fake_extract(path, **kw):
        seen.update(kw)
        return _result([_frame(_jpeg_bytes(320, 240), 1.0)], sampled_at=[1.0])

    _stub_extractor(monkeypatch, fake_extract)

    await cf.extract_cover_candidates(
        source_resource_id="500", user_id="u", num_frames=requested, repo=repo
    )
    assert seen["num_frames"] == expected


async def test_sampling_width_stays_inside_the_extractor_clamp_window(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``extract_frames`` 会把宽度钳到 [120, 1920]。封面层传 1080 是为了让
    9:16 源裁出 1080×1440 时一个像素都不放大 —— 这个常数要是漂到钳制窗口外，
    钳制会静默改掉它，封面就得放大而没人会发现。"""
    repo = _video_repo()
    seen: Dict[str, Any] = {}

    async def fake_extract(path, **kw):
        seen.update(kw)
        return _result([_frame(_jpeg_bytes(320, 240), 1.0)], sampled_at=[1.0])

    _stub_extractor(monkeypatch, fake_extract)

    await cf.extract_cover_candidates(
        source_resource_id="500", user_id="u", num_frames=1, repo=repo
    )
    assert 120 <= cf.COVER_FRAME_WIDTH <= 1920
    assert seen["frame_width"] == cf.COVER_FRAME_WIDTH
    # ffmpeg 侧也必须有上界（§7.2：所有等待都有上界）。
    assert seen["timeout_seconds"] > 0


# ============================================================
# 4. 优雅降级 —— extractor 的软失败必须变成类型化失败
# ============================================================


async def test_extractor_soft_failure_becomes_a_typed_422_not_a_500(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``extract_frames`` 的契约是"失败也不抛，返回空 attachments + error"——
    那是给聊天链路的优雅降级（没有画面也要把对话进行下去）。封面链路必须把那
    种软失败翻译成带 status_code 的硬失败，否则用户拿到的是一次没有原因的
    500（触发路径必须有类型化回显，CLAUDE.md 已知陷阱）。
    """
    repo = _video_repo()

    async def fake_extract(path, **kw):
        return _result([], sampled_at=[], duration=None, error="ffmpeg not installed")

    _stub_extractor(monkeypatch, fake_extract)

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.extract_cover_candidates(
            source_resource_id="500", user_id="u", num_frames=3, repo=repo
        )
    assert exc.value.status_code == 422
    # 原因要能透到用户面前，不能只留一句"失败了"。
    assert "ffmpeg not installed" in exc.value.detail


async def test_extractor_producing_no_frames_without_an_error_is_still_422(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _video_repo()

    async def fake_extract(path, **kw):
        return _result([], sampled_at=[])

    _stub_extractor(monkeypatch, fake_extract)

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.extract_cover_candidates(
            source_resource_id="500", user_id="u", num_frames=3, repo=repo
        )
    assert exc.value.status_code == 422


async def test_unreadable_video_degrades_to_422_instead_of_raising(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """视频不可读时 ffmpeg 每帧都失败 —— extractor 返回空列表（软失败），
    封面层必须把它变成一条用户能读懂的 422。这里用真的 ``extract_frames``、
    只打桩 ffmpeg 进程，好让软失败的形状是真的而不是我们编的。
    """
    from app.services.media.render import video_frame_extractor as vfx

    repo = _video_repo()
    monkeypatch.setattr(vfx.shutil, "which", lambda b: f"/usr/bin/{b}")

    async def fake_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"moov atom not found"))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        with pytest.raises(cf.CoverFrameError) as exc:
            await cf.extract_cover_candidates(
                source_resource_id="500", user_id="u", num_frames=3, repo=repo
            )
    assert exc.value.status_code == 422


async def test_video_file_missing_on_disk_is_404(local_download_root: Path) -> None:
    """文件系统形状的 file_path 指向一个不存在的文件 —— 这是"源没了"而不是
    "抽帧失败"，状态码要能区分（前者重试无意义）。"""
    repo = _video_repo(file_path="teams/scope-1/uploads/500/v1/gone.mp4")

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.extract_cover_candidates(
            source_resource_id="500", user_id="u", num_frames=3, repo=repo
        )
    assert exc.value.status_code == 404


async def test_file_path_escaping_the_download_root_is_400(
    local_download_root: Path,
) -> None:
    """containment guard：畸形/敌意的 rel_path 不能逃出 DOWNLOAD_PATH。"""
    repo = _video_repo(file_path="../../../etc/passwd")
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.extract_cover_candidates(
            source_resource_id="500", user_id="u", num_frames=3, repo=repo
        )
    assert exc.value.status_code == 400


async def test_total_deadline_expiry_is_reported_as_504(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """下载那一段本身没有超时，几个 GB 的源遇上存储抖动就是无限期挂起 ——
    外层总上界是唯一的止损，且必须表现为 504（重试可能有用）而不是 422
    （源坏了，重试无意义）。"""
    repo = _video_repo()
    monkeypatch.setattr(cf, "_TOTAL_DEADLINE_SECONDS", 0.05)

    async def slow_extract(path, **kw):
        await asyncio.sleep(5)
        raise AssertionError("deadline should have fired first")

    _stub_extractor(monkeypatch, slow_extract)

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.extract_cover_candidates(
            source_resource_id="500", user_id="u", num_frames=3, repo=repo
        )
    assert exc.value.status_code == 504


# ============================================================
# 5. 选帧 —— derive_cover_pair
# ============================================================


def _stage_frame_image(root: Path, rel: str, w: int, h: int) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_jpeg_bytes(w, h))


def _frame_repo(rel: str, rid: str = "900") -> FakeRepo:
    return FakeRepo(
        source={
            "id": rid,
            "file_path": rel,
            "file_type": "image",
            "mime_type": "image/jpeg",
            "filename": Path(rel).name,
        },
        item={"scope_id": "scope-1", "folder_id": "folder-9", "library_id": None},
    )


async def test_selected_frame_yields_one_3x4_and_one_4x3_cover(
    local_download_root: Path,
) -> None:
    rel = "teams/scope-1/derived/900/v1/cover-frame-1.5s-clip.jpg"
    _stage_frame_image(local_download_root, rel, 1080, 1920)
    repo = _frame_repo(rel)

    pair = await cf.derive_cover_pair(
        frame_resource_id="900", user_id="user-B", repo=repo
    )

    assert pair.source_frame_resource_id == "900"
    assert pair.vertical_resource_id != pair.horizontal_resource_id
    assert len(repo.created) == 2
    sizes = [
        Image.open(local_download_root / row["file_path"]).size for row in repo.updated
    ]
    assert sizes[0] == (1080, 1440)  # 3:4 —— 源 1080 宽，一个像素都不放大
    assert sizes[1] == (1080, 810)  # 4:3
    assert [r["filename"] for r in repo.created] == [
        "cover-vertical-cover-frame-1.5s-clip.jpg",
        "cover-horizontal-cover-frame-1.5s-clip.jpg",
    ]
    # 两张封面留在源帧所在的 scope / folder 里，不是孤儿。
    assert all(i["scope_id"] == "scope-1" for i in repo.items)
    assert all(i["folder_id"] == "folder-9" for i in repo.items)


async def test_landscape_frame_is_cropped_on_the_sides_for_the_vertical_cover(
    local_download_root: Path,
) -> None:
    rel = "teams/scope-1/derived/901/v1/wide.jpg"
    _stage_frame_image(local_download_root, rel, 1920, 1080)
    repo = _frame_repo(rel, rid="901")
    await cf.derive_cover_pair(frame_resource_id="901", user_id="u", repo=repo)
    sizes = [
        Image.open(local_download_root / row["file_path"]).size for row in repo.updated
    ]
    assert sizes[0] == (810, 1080)  # 3:4，保满高度裁两侧
    assert sizes[1] == (1440, 1080)  # 4:3，同上


async def test_missing_frame_resource_maps_to_404(local_download_root: Path) -> None:
    """``DeriveError`` 的状态码要原样透过来 —— router 只有一个 except 分支。"""
    repo = FakeRepo(source=None, item=None)
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair(frame_resource_id="nope", user_id="u", repo=repo)
    assert exc.value.status_code == 404


async def test_selecting_a_video_as_the_frame_maps_to_400(
    local_download_root: Path,
) -> None:
    repo = _video_repo()
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair(frame_resource_id="500", user_id="u", repo=repo)
    assert exc.value.status_code == 400


async def test_degenerate_frame_geometry_fails_422_with_the_failing_side_named(
    local_download_root: Path,
) -> None:
    """1px 宽的源上，4:3 区域在像素上会塌成零高度。``CropError`` 必须变成 422
    并说清是哪一版塌了，而不是让一个裸 ValueError 冒成 500。"""
    rel = "teams/scope-1/derived/902/v1/sliver.jpg"
    _stage_frame_image(local_download_root, rel, 1, 4000)
    repo = _frame_repo(rel, rid="902")
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair(frame_resource_id="902", user_id="u", repo=repo)
    assert exc.value.status_code == 422
    assert "horizontal" in exc.value.detail


async def test_unreadable_frame_bytes_map_to_422(local_download_root: Path) -> None:
    rel = "teams/scope-1/derived/903/v1/not-an-image.jpg"
    path = local_download_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"definitely not a jpeg")
    repo = _frame_repo(rel, rid="903")
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair(frame_resource_id="903", user_id="u", repo=repo)
    assert exc.value.status_code == 422


# ============================================================
# 6. 临时文件（§7.6）—— 失败路径必须走到那三个 finally
# ============================================================


async def test_download_temp_file_is_removed_when_extraction_raises(
    local_download_root: Path,
    isolated_tmpdir: Path,
    object_store: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``materialize`` 为 ``sb://`` 源落了一个临时文件；抽帧在 ``async with``
    体内抛异常时，它的 ``finally`` 仍必须删掉那个文件。

    这是实现模块 docstring 里"唯一要保证的事"的第一条。一次失败的抽帧留下几个
    GB 的视频临时文件，攒几十次就是磁盘打满 —— 而成功路径永远测不出这一点。
    """
    repo = _video_repo()
    staged: List[Path] = []

    async def exploding_extract(path, **kw):
        staged.append(Path(path))
        raise RuntimeError("ffmpeg wrapper blew up")

    _stub_extractor(monkeypatch, exploding_extract)

    with pytest.raises(RuntimeError):
        await cf.extract_cover_candidates(
            source_resource_id="500", user_id="u", num_frames=3, repo=repo
        )
    # 先证明确实有东西被落到了我们盯着的目录里，否则"目录是空的"是废话。
    assert staged and staged[0].parent == isolated_tmpdir
    assert list(isolated_tmpdir.iterdir()) == []


async def test_download_temp_file_is_removed_when_the_deadline_fires(
    local_download_root: Path,
    isolated_tmpdir: Path,
    object_store: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超时表现为 CancelledError 从 ``async with`` 内部抛出，上下文管理器的
    ``finally`` 照常执行 —— 这正是"整段包在 asyncio.timeout 里、不自己写循环
    等待"的理由，所以它需要一条断言守着。
    """
    repo = _video_repo()
    monkeypatch.setattr(cf, "_TOTAL_DEADLINE_SECONDS", 0.05)
    staged: List[Path] = []

    async def slow_extract(path, **kw):
        staged.append(Path(path))
        await asyncio.sleep(5)

    _stub_extractor(monkeypatch, slow_extract)

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.extract_cover_candidates(
            source_resource_id="500", user_id="u", num_frames=3, repo=repo
        )
    assert exc.value.status_code == 504
    assert staged and staged[0].parent == isolated_tmpdir
    assert list(isolated_tmpdir.iterdir()) == []


async def test_download_temp_file_is_removed_when_persistence_raises(
    local_download_root: Path,
    isolated_tmpdir: Path,
    object_store: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """落库失败发生在 ``materialize`` 的 ``async with`` 已经退出之后 —— 两个
    ``finally`` 互不依赖，下游炸了也不该让上游的清理欠账。"""
    repo = _video_repo()
    staged: List[Path] = []

    async def fake_extract(path, **kw):
        staged.append(Path(path))
        return _result([_frame(_jpeg_bytes(640, 360), 1.0)], sampled_at=[1.0])

    _stub_extractor(monkeypatch, fake_extract)

    async def exploding_create(data):
        raise RuntimeError("db down")

    monkeypatch.setattr(repo, "create_resource", exploding_create)

    with pytest.raises(RuntimeError):
        await cf.extract_cover_candidates(
            source_resource_id="500", user_id="u", num_frames=1, repo=repo
        )
    assert staged and staged[0].parent == isolated_tmpdir
    assert list(isolated_tmpdir.iterdir()) == []


async def test_extractor_temp_dir_is_removed_when_every_ffmpeg_call_fails(
    isolated_tmpdir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``extract_frames`` 用 ``TemporaryDirectory``（删整个目录而不是逐个文件，
    所以部分失败也不留残留）。ffmpeg 在这里是打桩的 —— 验的是清理契约，不是
    编解码，CI 上未必有真二进制。
    """
    from app.services.media.render import video_frame_extractor as vfx

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x14ftypisom")
    monkeypatch.setattr(vfx.shutil, "which", lambda b: f"/usr/bin/{b}")
    scratch_dirs: List[Path] = []

    async def fake_exec(*args, **kwargs):
        proc = AsyncMock()
        if "ffprobe" in args[0]:
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"30.0\n", b""))
            return proc
        # 最后一个位置参数是 ffmpeg 的输出路径 —— 它的父目录就是那个
        # TemporaryDirectory，记下来才能证明"目录是空的"不是废话。
        scratch_dirs.append(Path(args[-1]).parent)
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"boom"))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = await vfx.extract_frames(str(video), num_frames=3)

    assert result.attachments == []  # 软失败，不抛
    assert scratch_dirs and scratch_dirs[0].parent == isolated_tmpdir
    assert list(isolated_tmpdir.iterdir()) == []


async def test_persist_temp_file_is_removed_when_the_disk_write_fails(
    local_download_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``persist_derived_image`` 先把 bytes 落到 DOWNLOAD_PATH 下的临时文件再
    原子改名。改名失败（磁盘满 / 权限）时它的 ``finally`` 必须删掉那个 tmp，
    否则每次失败的裁切都在资源卷根目录留一个匿名文件 —— 没有名字、没有属主、
    没人会想到去清。
    """
    import os

    from app.services.canvas import derive_persistence

    mid_flight: List[Path] = []

    def exploding_replace(src, dst):
        # 此刻 tmp 还躺在 DOWNLOAD_PATH 根下 —— 记一笔，好让下面"根目录没有
        # 文件"这条断言有对照，而不是在数一个本来就空的目录。
        mid_flight.extend(p for p in local_download_root.iterdir() if p.is_file())
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "replace", exploding_replace)

    with pytest.raises(OSError):
        await derive_persistence.persist_derived_image(
            FakeRepo(),
            user_id="u",
            scope_id="1",
            folder_id=None,
            library_id=None,
            filename="cover-vertical-x.jpg",
            image_bytes=_jpeg_bytes(64, 64),
            mime_type="image/jpeg",
        )

    assert len(mid_flight) == 1, "写失败的那一刻应当正好有一个 tmp 在盘上"
    assert [p for p in local_download_root.iterdir() if p.is_file()] == []
