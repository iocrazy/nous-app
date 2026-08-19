"""Distribution 封面抽帧 —— service 层测试。

四组，对应实现里四条各自能独立坏掉的性质：

1. **裁切几何**（``center_crop_region``）—— 纯函数，比例必须精确，极端源不能
   算出零/负尺寸的区域。
2. **编排**（``extract_cover_candidates`` / ``derive_cover_pair``）—— 抽帧
   **一行库都不写**、时间戳对齐、预览预算、参数钳制、坏帧跳过；选帧按时间点
   重抽同一帧再裁。
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


async def test_sampling_a_video_writes_no_library_rows_at_all(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """抽帧**一行 resources 都不写** —— 这是这次改动的全部要点。

    候选帧原本每张落一行，继承源视频的 folder / library / scope，于是混进用户
    放那个视频的文件夹里，跟正经素材没有任何结构化字段能区分。

    ⚠️ 单看"没有新建 resources 行"是一条会假绿的断言：抽帧流程要是因为别的原因
    压根没跑，它同样成立。所以先钉住**流程确实跑完了**（extractor 真被调用、
    候选数与帧数一致、每张预览都能解成一张真图），再断言四张写表都是空的。
    """
    repo = _video_repo()
    frames = [_jpeg_bytes(1080, 1920) for _ in range(3)]
    calls: List[str] = []

    async def fake_extract(path, **kw):
        calls.append(path)
        return _result(
            [_frame(b, ts) for b, ts in zip(frames, (1.5, 15.0, 28.5))],
            sampled_at=[1.5, 15.0, 28.5],
        )

    _stub_extractor(monkeypatch, fake_extract)

    out = await cf.extract_cover_candidates(
        source_resource_id="500", num_frames=3, repo=repo
    )

    # ① 流程真的跑完了。
    assert len(calls) == 1
    assert out.source_resource_id == "500"
    assert out.duration_seconds == 30.0
    assert len(out.candidates) == 3
    assert [c.index for c in out.candidates] == [0, 1, 2]
    assert [c.timestamp_seconds for c in out.candidates] == [1.5, 15.0, 28.5]
    # ② 每个候选真的带着一张能显示的图（不是空串、不是坏 base64）。
    for c in out.candidates:
        assert c.preview_data_url.startswith("data:image/jpeg;base64,")
        raw = base64.b64decode(c.preview_data_url.split(",", 1)[1])
        assert Image.open(BytesIO(raw)).size == (c.preview_width, c.preview_height)
    # ③ 只有在①②都成立的前提下，"库里什么都没多"才是有意义的断言。
    assert repo.created == []
    assert repo.updated == []
    assert repo.versions == []
    assert repo.items == []


async def test_untimestamped_frames_fail_with_a_reason_instead_of_dead_tiles(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffprobe 探不到时长时 ``extract_frames`` 只产帧、不产时间戳。

    时间点现在是选帧的**唯一坐标**（候选帧不落库），所以那种输出没法用：展示
    出来会是一排点了没反应的图。必须变成一条说得清原因的类型化失败。
    """
    repo = _video_repo()

    async def fake_extract(path, **kw):
        return _result(
            [_frame(_jpeg_bytes(640, 360)) for _ in range(2)],
            sampled_at=[],
            duration=None,
        )

    _stub_extractor(monkeypatch, fake_extract)

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.extract_cover_candidates(
            source_resource_id="500", num_frames=2, repo=repo
        )
    assert exc.value.status_code == 422
    assert "duration" in exc.value.detail


async def test_one_undecodable_frame_is_skipped_and_the_rest_survive(
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
        source_resource_id="500", num_frames=3, repo=repo
    )
    assert len(out.candidates) == 2
    # 跳过的那一帧不会让后面的时间戳错位 —— 时间点现在是选帧的坐标，错位就是
    # "挑 A 得到 B"。
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
    calls: List[str] = []

    async def fake_extract(path, **kw):
        calls.append(path)
        return _result([broken, broken], sampled_at=[1.0, 2.0])

    _stub_extractor(monkeypatch, fake_extract)

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.extract_cover_candidates(
            source_resource_id="500", num_frames=2, repo=repo
        )
    assert exc.value.status_code == 422
    # extractor 确实被调用过 —— 否则"没建行"是废话。
    assert len(calls) == 1
    assert repo.created == []


async def test_preview_payload_stays_inside_the_realtime_budget(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整份候选清单必须能塞进 Realtime 的行上限，**即使画面是最坏情况**。

    预览走 ``task_tracking.metadata``，行超限会被整条丢弃 —— 表现是"任务完成了
    但一张候选都没有"，跟"抽帧失败"完全不同却一样没有原因。所以这里喂纯噪声
    （JPEG 最难压的输入，比任何真实画面都糟），按 MAX_COVER_FRAMES 满打满算，
    断言 base64 总量仍远低于 1 MB。
    """
    import random

    repo = _video_repo()
    rnd = random.Random(7)
    noise = Image.new("RGB", (cf.COVER_PREVIEW_WIDTH, 427))
    noise.putdata(
        [
            (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
            for _ in range(cf.COVER_PREVIEW_WIDTH * 427)
        ]
    )
    buf = BytesIO()
    noise.save(buf, format="JPEG", quality=95)
    worst = buf.getvalue()
    # 前提：这张图**本来**是超预算的，否则下面的断言测不到收缩逻辑。
    assert len(worst) > cf._PREVIEW_MAX_BYTES

    n = cf.MAX_COVER_FRAMES

    async def fake_extract(path, **kw):
        return _result(
            [_frame(worst, float(i)) for i in range(n)],
            sampled_at=[float(i) for i in range(n)],
        )

    _stub_extractor(monkeypatch, fake_extract)

    out = await cf.extract_cover_candidates(
        source_resource_id="500", num_frames=n, repo=repo
    )
    assert len(out.candidates) == n
    total = sum(len(c.preview_data_url) for c in out.candidates)
    assert total < 400 * 1024
    # 每一张仍然是一张能打开的图 —— 收缩不能把预览压成垃圾。
    for c in out.candidates:
        raw = base64.b64decode(c.preview_data_url.split(",", 1)[1])
        assert Image.open(BytesIO(raw)).size[0] >= cf._PREVIEW_MIN_WIDTH


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
    """每帧都是一次 ffmpeg seek + 一份要走 Realtime 的预览，所以封面这层的上限
    比 extractor 自己的 32 更严。钳制失效 = 一次点击跑 99 次 seek，并把
    metadata 撑到 Realtime 丢行。"""
    repo = _video_repo()
    seen: Dict[str, Any] = {}

    async def fake_extract(path, **kw):
        seen.update(kw)
        return _result([_frame(_jpeg_bytes(320, 240), 1.0)], sampled_at=[1.0])

    _stub_extractor(monkeypatch, fake_extract)

    await cf.extract_cover_candidates(
        source_resource_id="500", num_frames=requested, repo=repo
    )
    assert seen["num_frames"] == expected


async def test_sampling_uses_the_small_preview_width_not_the_crop_width(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """抽帧只为给人挑，用 240；1080 留给选帧时的重抽。

    两个常数都必须落在 ``extract_frames`` 的钳制窗口 [120, 1920] 内 —— 漂到窗口
    外时钳制会**静默**改掉它，没人会发现。1080 那个尤其重要：9:16 源裁 3:4 正好
    1080×1440，一个像素都不放大。
    """
    repo = _video_repo()
    seen: Dict[str, Any] = {}

    async def fake_extract(path, **kw):
        seen.update(kw)
        return _result([_frame(_jpeg_bytes(320, 240), 1.0)], sampled_at=[1.0])

    _stub_extractor(monkeypatch, fake_extract)

    await cf.extract_cover_candidates(source_resource_id="500", num_frames=1, repo=repo)
    assert 120 <= cf.COVER_PREVIEW_WIDTH <= 1920
    assert 120 <= cf.COVER_FRAME_WIDTH <= 1920
    assert cf.COVER_PREVIEW_WIDTH < cf.COVER_FRAME_WIDTH
    assert seen["frame_width"] == cf.COVER_PREVIEW_WIDTH
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
            source_resource_id="500", num_frames=3, repo=repo
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
            source_resource_id="500", num_frames=3, repo=repo
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
                source_resource_id="500", num_frames=3, repo=repo
            )
    assert exc.value.status_code == 422


async def test_video_file_missing_on_disk_is_404(local_download_root: Path) -> None:
    """文件系统形状的 file_path 指向一个不存在的文件 —— 这是"源没了"而不是
    "抽帧失败"，状态码要能区分（前者重试无意义）。"""
    repo = _video_repo(file_path="teams/scope-1/uploads/500/v1/gone.mp4")

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.extract_cover_candidates(
            source_resource_id="500", num_frames=3, repo=repo
        )
    assert exc.value.status_code == 404


async def test_file_path_escaping_the_download_root_is_400(
    local_download_root: Path,
) -> None:
    """containment guard：畸形/敌意的 rel_path 不能逃出 DOWNLOAD_PATH。"""
    repo = _video_repo(file_path="../../../etc/passwd")
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.extract_cover_candidates(
            source_resource_id="500", num_frames=3, repo=repo
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
            source_resource_id="500", num_frames=3, repo=repo
        )
    assert exc.value.status_code == 504


# ============================================================
# 5a. 选帧（当前路径）—— derive_cover_pair，按时间点重抽
# ============================================================


def _stub_reextract(monkeypatch: pytest.MonkeyPatch, frame_bytes_or_none):
    """打桩"按秒数重抽一帧"这一步，记录它收到的时间点。

    打在 ``extract_frame_at`` 而不是更外层，是为了让被验证的仍然是本模块的编排
    （materialize / 超时翻译 / 裁切 / 落库），只把 ffmpeg 换掉。
    """
    from app.services.media.render import video_frame_extractor as vfx

    seen: List[Dict[str, Any]] = []

    async def fake(path, **kw):
        seen.append({"path": path, **kw})
        return frame_bytes_or_none

    monkeypatch.setattr(vfx, "extract_frame_at", fake)
    return seen


async def test_picking_a_timestamp_re_reads_that_exact_second_and_crops_it(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户挑的是一个时间点，服务端必须回到**源视频**、在**同一秒**重抽。

    这条钉住的是"挑 A 得到 B"最可能的退化：时间点被丢掉、被取整、或者传给了
    别的资源。所以断言的是重抽真的发生过、收到的秒数逐位相等、宽度是裁切宽度
    （而不是预览宽度），以及产出的两张封面尺寸对得上。
    """
    repo = _video_repo()
    seen = _stub_reextract(monkeypatch, _jpeg_bytes(1080, 1920))

    pair = await cf.derive_cover_pair(
        source_resource_id="500",
        timestamp_seconds=15.25,
        user_id="user-B",
        repo=repo,
    )

    assert len(seen) == 1
    assert seen[0]["timestamp_seconds"] == 15.25
    assert seen[0]["frame_width"] == cf.COVER_FRAME_WIDTH
    assert seen[0]["timeout_seconds"] > 0
    assert pair.source_frame_resource_id == "500"
    assert pair.vertical_resource_id != pair.horizontal_resource_id
    # 只有两张成品封面落库 —— 候选帧一张都没有。
    assert len(repo.created) == 2
    sizes = [
        Image.open(local_download_root / row["file_path"]).size for row in repo.updated
    ]
    assert sizes[0] == (1080, 1440)  # 3:4
    assert sizes[1] == (1080, 810)  # 4:3
    assert [r["filename"] for r in repo.created] == [
        "cover-vertical-clip.jpg",
        "cover-horizontal-clip.jpg",
    ]
    # 两张封面留在源视频所在的 scope / folder 里，不是孤儿。
    assert all(i["scope_id"] == "scope-1" for i in repo.items)
    assert all(i["folder_id"] == "folder-9" for i in repo.items)
    assert all(r["creator_id"] == "user-B" for r in repo.created)


async def test_a_frame_that_cannot_be_re_read_says_so_instead_of_blanking(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重抽拿不到画面（时间点越界、文件被换过、解码失败）必须是类型化 422。

    静默产出一张空白封面、或者冒一个没有原因的 500，都是这条路径上最坏的结果：
    用户会带着一个错的封面发出去。
    """
    repo = _video_repo()
    seen = _stub_reextract(monkeypatch, None)

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair(
            source_resource_id="500", timestamp_seconds=9.5, user_id="u", repo=repo
        )
    assert len(seen) == 1  # 确实试过了 —— 否则下面的断言是废话
    assert exc.value.status_code == 422
    assert "9.5" in exc.value.detail
    assert repo.created == []


@pytest.mark.parametrize("bad", [-1.0, float("nan"), float("inf")])
async def test_a_malformed_timestamp_is_a_400_not_a_500(
    local_download_root: Path, monkeypatch: pytest.MonkeyPatch, bad: float
) -> None:
    """时间点是用户可达的输入（前端原样回传），畸形值必须是说得清的 400。

    NaN / inf 一路飘到 ffmpeg 的 ``-ss`` 会变成一次没有原因的服务端错误。
    """
    repo = _video_repo()
    seen = _stub_reextract(monkeypatch, _jpeg_bytes(64, 64))
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair(
            source_resource_id="500", timestamp_seconds=bad, user_id="u", repo=repo
        )
    assert exc.value.status_code == 400
    # 坏输入在碰 ffmpeg 之前就被挡住了。
    assert seen == []


async def test_selecting_from_a_missing_source_video_is_404(
    local_download_root: Path,
) -> None:
    repo = FakeRepo(source=None, item=None)
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair(
            source_resource_id="nope", timestamp_seconds=1.0, user_id="u", repo=repo
        )
    assert exc.value.status_code == 404


async def test_selecting_from_an_image_source_is_400(local_download_root: Path) -> None:
    """选帧的源必须是视频 —— 图片没有"第 N 秒"可言。"""
    repo = FakeRepo(
        source={
            "id": "600",
            "file_path": "teams/scope-1/uploads/600/v1/pic.jpg",
            "file_type": "image",
            "mime_type": "image/jpeg",
            "filename": "pic.jpg",
        },
        item={"scope_id": "scope-1", "folder_id": None, "library_id": None},
    )
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair(
            source_resource_id="600", timestamp_seconds=1.0, user_id="u", repo=repo
        )
    assert exc.value.status_code == 400


async def test_re_read_deadline_expiry_is_reported_as_504(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """选帧现在也要 materialize —— 缓存没命中就得重新拉源文件，而这条挂在同步
    HTTP 请求上。等待必须有上界，且表现为 504（同一帧再试一次通常就好），不能
    是 422（那会让用户去换一帧，换哪一帧都一样）。"""
    from app.services.media.render import video_frame_extractor as vfx

    repo = _video_repo()
    monkeypatch.setattr(cf, "_SELECT_DEADLINE_SECONDS", 0.05)

    async def slow(path, **kw):
        await asyncio.sleep(5)
        raise AssertionError("deadline should have fired first")

    monkeypatch.setattr(vfx, "extract_frame_at", slow)

    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair(
            source_resource_id="500", timestamp_seconds=1.0, user_id="u", repo=repo
        )
    assert exc.value.status_code == 504


async def test_degenerate_re_read_geometry_fails_422_naming_the_side(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1px 宽的帧上，4:3 区域在像素上会塌成零高度。``CropError`` 必须变成 422
    并说清是哪一版塌了，而不是让一个裸 ValueError 冒成 500。"""
    repo = _video_repo()
    _stub_reextract(monkeypatch, _jpeg_bytes(1, 4000))
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair(
            source_resource_id="500", timestamp_seconds=1.0, user_id="u", repo=repo
        )
    assert exc.value.status_code == 422
    assert "horizontal" in exc.value.detail


async def test_undecodable_re_read_bytes_map_to_422(
    local_download_root: Path, object_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _video_repo()
    _stub_reextract(monkeypatch, b"definitely not a jpeg")
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair(
            source_resource_id="500", timestamp_seconds=1.0, user_id="u", repo=repo
        )
    assert exc.value.status_code == 422


# ============================================================
# 5b. 选帧（部署错峰兼容路径）—— derive_cover_pair_from_frame
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


async def test_legacy_frame_resource_still_yields_one_3x4_and_one_4x3_cover(
    local_download_root: Path,
) -> None:
    """错峰窗口里的旧前端手里只有 resource id。那一次点击必须仍然成功，否则
    "候选已拿到、还没点"的用户会在发布链上线的瞬间吃一个 422。"""
    rel = "teams/scope-1/derived/900/v1/cover-frame-1.5s-clip.jpg"
    _stage_frame_image(local_download_root, rel, 1080, 1920)
    repo = _frame_repo(rel)

    pair = await cf.derive_cover_pair_from_frame(
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
    await cf.derive_cover_pair_from_frame(
        frame_resource_id="901", user_id="u", repo=repo
    )
    sizes = [
        Image.open(local_download_root / row["file_path"]).size for row in repo.updated
    ]
    assert sizes[0] == (810, 1080)  # 3:4，保满高度裁两侧
    assert sizes[1] == (1440, 1080)  # 4:3，同上


async def test_missing_frame_resource_maps_to_404(local_download_root: Path) -> None:
    """``DeriveError`` 的状态码要原样透过来 —— router 只有一个 except 分支。"""
    repo = FakeRepo(source=None, item=None)
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair_from_frame(
            frame_resource_id="nope", user_id="u", repo=repo
        )
    assert exc.value.status_code == 404


async def test_selecting_a_video_as_the_frame_maps_to_400(
    local_download_root: Path,
) -> None:
    repo = _video_repo()
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair_from_frame(
            frame_resource_id="500", user_id="u", repo=repo
        )
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
        await cf.derive_cover_pair_from_frame(
            frame_resource_id="902", user_id="u", repo=repo
        )
    assert exc.value.status_code == 422
    assert "horizontal" in exc.value.detail


async def test_unreadable_frame_bytes_map_to_422(local_download_root: Path) -> None:
    rel = "teams/scope-1/derived/903/v1/not-an-image.jpg"
    path = local_download_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"definitely not a jpeg")
    repo = _frame_repo(rel, rid="903")
    with pytest.raises(cf.CoverFrameError) as exc:
        await cf.derive_cover_pair_from_frame(
            frame_resource_id="903", user_id="u", repo=repo
        )
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
            source_resource_id="500", num_frames=3, repo=repo
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
            source_resource_id="500", num_frames=3, repo=repo
        )
    assert exc.value.status_code == 504
    assert staged and staged[0].parent == isolated_tmpdir
    assert list(isolated_tmpdir.iterdir()) == []


async def test_select_download_temp_file_is_removed_when_persistence_raises(
    local_download_root: Path,
    isolated_tmpdir: Path,
    object_store: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """落库失败发生在 ``materialize`` 的 ``async with`` 已经退出之后 —— 两个
    ``finally`` 互不依赖，下游炸了也不该让上游的清理欠账。

    落库现在只在**选帧**这条路径上发生（抽帧不写库了），所以这条断言跟着搬到
    ``derive_cover_pair``：它同样要 materialize 源视频，同样在之后写两行。
    """
    from app.services.media.render import video_frame_extractor as vfx

    repo = _video_repo()
    staged: List[Path] = []

    async def fake_frame_at(path, **kw):
        staged.append(Path(path))
        return _jpeg_bytes(640, 360)

    monkeypatch.setattr(vfx, "extract_frame_at", fake_frame_at)

    async def exploding_create(data):
        raise RuntimeError("db down")

    monkeypatch.setattr(repo, "create_resource", exploding_create)

    with pytest.raises(RuntimeError):
        await cf.derive_cover_pair(
            source_resource_id="500", timestamp_seconds=1.0, user_id="u", repo=repo
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
