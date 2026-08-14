"""Distribution — 封面抽帧（从已选视频里均匀取帧，让用户挑一张做封面）。

发布模块的 ``publish_tasks.cover_vertical_resource_id`` /
``cover_horizontal_resource_id`` 从 migration 356 起就存在，浏览器侧的
``_set_cover`` 也早就实现了，唯独缺"封面从哪来"。这个模块补的就是那一段。

分两步，因为两步的代价差了两个数量级：

    抽帧（慢，DBOS workflow）   materialize 视频 → ffmpeg 均匀取 N 帧
                               → 每帧落成一个 resources 行（候选）
    选帧（快，同步 REST）       用户挑一帧 → 居中裁出 3:4 / 4:3 两张
                               → 两个新 resources 行 → 写回 publish_tasks

三层复用，本模块不自己实现任何一层
==================================
1. ``media_storage.materialize()`` —— 素材在对象存储，而 ffmpeg 要本地路径。
   它同时是"两种 file_path 形状的适配器"（``sb://`` 流式落盘 / 文件系统直接
   给真实路径）、containment guard（``..`` 逃不出 DOWNLOAD_PATH）、以及本机
   NVMe 的 LRU 读缓存。**没有走 ``get_resource_media_url()`` + HTTP 下载**：
   那条路是给"对端 API 主动来拉"设计的（抖音发布、volcengine ASR），我们自己
   要读同一个进程能直接读到的文件，绕一圈签名 URL 只会多一次网关往返、丢掉
   磁盘缓存，还得自己管临时文件。
2. ``video_frame_extractor.extract_frames()`` —— 均匀采样、参数钳制、
   ffmpeg 失败时优雅降级成 ``result.error`` 而不是抛异常。原本是给多模态
   聊天做视频理解的，能力正好，一行没改。
3. ``canvas.image_crop.crop_normalized()`` + ``derive_persistence
   .persist_derived_image()`` —— 裁切与"bytes → resources 行"落地。选帧那一步
   因此完全不碰视频、不碰 ffmpeg，就是一次普通的图片 derive。

临时文件（§7.6：不该留在磁盘上的东西一件都不留）
==============================================
**本模块一个临时文件都不创建**，所以"成功 / 失败 / 异常"三条路径的清理不是
这里新写的守则，而是三个被复用组件各自已经拥有的：

    materialize()            finally: Path(tmp).unlink(missing_ok=True)
    extract_frames()         with tempfile.TemporaryDirectory()（删目录而非
                             逐个文件，部分失败也不留残留）
    persist_derived_image()  finally: Path(tmp_name).unlink(missing_ok=True)

唯一要保证的是**异常/超时路径也能走到那三个 finally**：整段抽帧包在
``asyncio.timeout`` 里而不是自己写循环等待，超时表现为 CancelledError 从
``async with`` 内部抛出，两个上下文管理器的 ``finally`` 照常执行。
``tests/test_cover_frames.py`` 对三条路径各有一条断言。

裁切策略见 ``center_crop_region`` 的 docstring。
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

from loguru import logger

from app.services.canvas.derive_persistence import (
    ResourceRepoProtocol,
    persist_derived_image,
)
from app.services.canvas.image_crop import CropError, CropRegion, crop_normalized
from app.services.media.render.video_frame_extractor import extract_frames

# ── 抖音封面的两个比例 ────────────────────────────────────────────
# 竖版 3:4、横版 4:3（都是 width/height）。抖音创作页那两个上传位就是这两个
# 比例，比例不对会被平台自己再裁一刀 —— 与其让平台猜，不如我们裁准。
COVER_VERTICAL_ASPECT = 3 / 4
COVER_HORIZONTAL_ASPECT = 4 / 3

# 候选帧的采样宽度。1080 不是随手取的：抖音竖版封面推荐 1080×1440，而
# 9:16 竖屏源（短视频的绝对主流）抽出来就是 1080×1920，居中裁 3:4 正好
# 1080×1440，一个像素都不用放大。extract_frames 内部钳制区间是 [120, 1920]。
COVER_FRAME_WIDTH = 1080

# 候选帧数。每一帧都会落成一个 resources 行（见下方 persist 段的说明），所以
# 上限比 extract_frames 自己的 32 更严 —— 12 张已经远超"挑一张封面"的需要，
# 再多只是往用户素材库里灌垃圾。
DEFAULT_COVER_FRAMES = 6
MAX_COVER_FRAMES = 12

# 两道上界（§7.2：所有等待必须有上界）。
# ffmpeg 侧的上界由 extract_frames 的 timeout_seconds 承担，它会再按帧数均分。
_EXTRACT_TIMEOUT_SECONDS = 180.0
# 外层总上界要把 materialize 的下载也罩进去 —— 那一步本身没有超时，几个 GB
# 的源视频遇上存储抖动就是无限期挂起。10 分钟之后一律放弃。
_TOTAL_DEADLINE_SECONDS = 600.0

_COVER_MIME = "image/jpeg"


class CoverFrameError(Exception):
    """类型化失败 —— 带上 router 该回的 HTTP 状态码。

    与 ``canvas.derive_persistence.DeriveError`` 同款形状（``status_code`` /
    ``detail``），因为两边失败的语义是一样的：源不存在 404、源不合法 400、
    底层工具没能产出可用结果 422。抽帧链路里凡是"能预期的坏情况"都必须落到
    这个类型上，而不是让 ffmpeg / 存储的原始异常冒成 500 —— 触发路径必须有
    类型化回显（CLAUDE.md 已知陷阱）。
    """

    def __init__(self, *, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class CoverCandidate:
    """一个候选帧：已经落库的 resource + 它在源视频里的时间点。"""

    resource_id: str
    timestamp_seconds: Optional[float]
    """采样时间点。``None`` 表示 ffprobe 拿不到时长、``extract_frames`` 走了
    fps 兜底分支 —— 那条分支只产出帧、不产出时间戳，帧仍然可用，所以这里用
    可空而不是编一个假时间。"""
    width: int
    height: int
    filename: str

    def as_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "timestamp_seconds": self.timestamp_seconds,
            "width": self.width,
            "height": self.height,
            "filename": self.filename,
        }


@dataclass(frozen=True)
class CoverCandidates:
    source_resource_id: str
    duration_seconds: Optional[float]
    candidates: tuple[CoverCandidate, ...]

    def as_dict(self) -> dict:
        return {
            "source_resource_id": self.source_resource_id,
            "duration_seconds": self.duration_seconds,
            "candidates": [c.as_dict() for c in self.candidates],
        }


@dataclass(frozen=True)
class CoverPair:
    """一帧裁出来的两张封面。"""

    vertical_resource_id: str
    horizontal_resource_id: str
    source_frame_resource_id: str


@dataclass(frozen=True)
class SourceVideo:
    """校验通过的源视频 + 它所在的 scope（决定候选帧落到哪）。"""

    resource: dict
    file_path: str
    scope_id: str
    folder_id: Optional[str]
    library_id: Optional[str]

    @property
    def filename(self) -> str:
        return str(self.resource.get("filename") or "video")


# ── 纯函数：裁切几何 ──────────────────────────────────────────────


def center_crop_region(
    source_width: int, source_height: int, target_aspect: float
) -> CropRegion:
    """求"能塞进源图的最大 target_aspect 矩形"，居中。

    返回归一化 ``[0,1]`` 区域，直接喂给 ``crop_normalized``。

    为什么是居中裁切
    ===============
    这是在**没有内容理解**的前提下能给出的最不坏的默认值，理由有三条：

    1. 短视频的主体（人脸、产品）绝大多数落在画面中心区域 —— 拍摄者取景时
       就是这么构的图，这不是我们的假设，是拍摄惯例。
    2. 主流源是 9:16 竖屏。竖版 3:4 对它只裁掉上下各 ~21%，主体几乎必然保住；
       这是两个输出里更重要的那个（抖音竖版封面才是信息流里露出的那张）。
    3. 偏移量没有依据就不该编。我一度想给横版 4:3 加一个"向上偏"的经验值
       （人脸常在上三分之一，而 4:3 从 9:16 里只能保住 42% 的高度，居中确实
       可能切到头顶），但那个数字无法验证，把猜测伪装成决策比居中更糟。

    **真正的解法是显著性/人脸检测来定锚点**，那是这个函数的自然升级位：签名
    不用变，只是 anchor 从固定 0.5 变成检测出来的值。在那之前，用户想要别的
    构图有现成出口 —— 候选帧本身就是普通 resources 行，canvas 已有的
    crop-derive 链路（``/api/v1/canvas/derive/crop``）可以任意重裁。

    Raises:
        CoverFrameError: 源尺寸不合法（400）。
    """
    if source_width <= 0 or source_height <= 0:
        raise CoverFrameError(
            status_code=400,
            detail=f"invalid source dimensions: {source_width}x{source_height}",
        )
    if target_aspect <= 0:
        raise CoverFrameError(
            status_code=400, detail=f"invalid target aspect: {target_aspect}"
        )

    source_aspect = source_width / source_height
    if source_aspect > target_aspect:
        # 源比目标更宽 → 保满高度，裁两侧。
        width = target_aspect / source_aspect
        return CropRegion(x=(1.0 - width) / 2.0, y=0.0, width=width, height=1.0)
    # 源比目标更高（或恰好相等）→ 保满宽度，裁上下。
    height = source_aspect / target_aspect
    return CropRegion(x=0.0, y=(1.0 - height) / 2.0, width=1.0, height=min(height, 1.0))


def _decode_data_url(data_url: str) -> Optional[bytes]:
    """``data:image/jpeg;base64,xxx`` → 原始字节。解不开返回 None。

    ``extract_frames`` 的产出是 data URL（它本来喂给多模态模型），而我们要的是
    能落库的 bytes，这里只做那一次反向转换。
    """
    if not data_url or "," not in data_url:
        return None
    try:
        return base64.b64decode(data_url.split(",", 1)[1])
    except (ValueError, TypeError) as exc:
        logger.warning(f"[cover_frames] could not decode frame data URL: {exc!r}")
        return None


def _image_size(image_bytes: bytes) -> tuple[int, int]:
    """读图片像素尺寸。失败抛 CoverFrameError（422，"帧不可用"）。"""
    from PIL import Image

    try:
        with Image.open(BytesIO(image_bytes)) as img:
            return img.size
    except Exception as exc:  # noqa: BLE001 — Pillow 的解码异常族很杂
        raise CoverFrameError(
            status_code=422, detail=f"extracted frame is not a readable image: {exc}"
        ) from exc


def _is_video(resource: dict) -> bool:
    if resource.get("file_type") == "video":
        return True
    return (resource.get("mime_type") or "").lower().startswith("video/")


async def load_source_video(
    repo: ResourceRepoProtocol, source_resource_id: str
) -> SourceVideo:
    """查源视频 + 它的 scope 归属。

    与 ``derive_persistence.load_source_image`` 同构（同样的 404/400 口径、
    同样从第一条 ``resource_items`` 取 scope），只是把"必须是图片"换成"必须是
    视频"，并且**不读文件内容** —— 视频动辄几个 GB，不能像图片那样整个读进
    内存，抽帧走的是 ``materialize`` 给出的本地路径。

    ⚠️ 调用方必须已经建立 ambient tenant scope（HTTP 侧 request_scope /
    ScopedRequestDep，workflow 侧 request_scope）。没有 scope 时
    ``get_resource_by_id`` 抛 ``UnscopedQueryError``，本函数**故意不接**它 ——
    那是调用方的缺陷，不是"源不存在"，让它冒到 router 变成 500。曾经它被 repo
    吞成 None，于是下面这个 404 把服务端 bug 说成了"该视频已不可用"。

    Raises:
        CoverFrameError: 404 资源/文件不存在，400 不是视频或没有 scope 归属。
        UnscopedQueryError: 调用方没开 tenant scope（穿透，不翻译成 404）。
    """
    source = await repo.get_resource_by_id(source_resource_id)
    if not source:
        raise CoverFrameError(status_code=404, detail="source resource not found")
    if not _is_video(source):
        raise CoverFrameError(
            status_code=400, detail="cover extraction is only valid for video resources"
        )
    file_path = source.get("file_path")
    if not file_path:
        raise CoverFrameError(
            status_code=400, detail="source resource has no file on disk yet"
        )

    item = await repo.get_first_resource_item(source_resource_id)
    if not item:
        raise CoverFrameError(
            status_code=400, detail="source resource has no scope link"
        )
    scope_id = str(item.get("scope_id") or "")
    if not scope_id:
        raise CoverFrameError(status_code=400, detail="source resource has no scope_id")

    return SourceVideo(
        resource=source,
        file_path=str(file_path),
        scope_id=scope_id,
        folder_id=item.get("folder_id"),
        library_id=item.get("library_id"),
    )


def _candidate_filename(source_filename: str, index: int, ts: Optional[float]) -> str:
    stem = (source_filename.rsplit(".", 1)[0] or "video")[:60]
    marker = f"{ts:.1f}s" if ts is not None else f"{index:02d}"
    return f"cover-frame-{marker}-{stem}.jpg"


async def extract_cover_candidates(
    *,
    source_resource_id: str,
    user_id: str,
    num_frames: int = DEFAULT_COVER_FRAMES,
    repo: Optional[ResourceRepoProtocol] = None,
) -> CoverCandidates:
    """从源视频均匀抽 ``num_frames`` 帧，每帧落成一个 resources 行。

    候选帧为什么要落库，而不是塞进 task_tracking 的 metadata：metadata 是
    jsonb，且会经 Realtime 推给每个订阅者 —— 6 张 1080 宽的 JPEG 换成 base64
    大约 1.5 MB，那是给实时通道灌洪水。落成 resources 行之后前端拿到的是普通
    的 media URL，选帧那一步也因此变成一次普通的图片裁切（不必再碰视频）。

    代价是一次抽帧会在素材库里多出 N 行 ``source_type='derived'`` 的图片，
    与 canvas 的 crop/split/grid derive 完全同款，落在源视频同一个 scope /
    folder 下，不是孤儿。

    归属：scope / folder / library 跟随源视频，``creator_id`` 是**发起操作的
    人**而不是源视频的作者 —— 这是 ``persist_derived_image`` 既有的口径（团队
    成员基于同事的素材派生，产物归派生者、留在同一个团队 scope），这里没有
    另立一套。

    Raises:
        CoverFrameError: 404/400 源不合法，422 抽不出可用帧，504 超时。
    """
    from app.repositories.resources_repository import ResourcesRepository

    repo = repo or ResourcesRepository()
    num_frames = max(1, min(MAX_COVER_FRAMES, num_frames))

    source = await load_source_video(repo, source_resource_id)
    frames = await _extract_frames_from_storage(source.file_path, num_frames)

    candidates: list[CoverCandidate] = []
    for index, attachment in enumerate(frames.attachments):
        image_bytes = _decode_data_url(attachment.data_url or "")
        if not image_bytes:
            # 单帧解码失败不该毁掉整次抽帧 —— 其余帧仍然是好的候选。全军覆没
            # 的情况由下面的空列表检查兜底。
            logger.warning(
                f"[cover_frames] frame {index} of resource {source_resource_id} "
                f"produced no bytes; skipping"
            )
            continue
        # sampled_at_seconds 与 attachments 只在"时长已知"的主分支上是等长的；
        # ffprobe 失败时 extract_frames 走 fps 兜底，只产帧不产时间戳。按下标
        # 硬取会 IndexError，所以这里显式对齐。
        ts = (
            frames.sampled_at_seconds[index]
            if index < len(frames.sampled_at_seconds)
            else None
        )
        width, height = _image_size(image_bytes)
        row = await persist_derived_image(
            repo,
            user_id=user_id,
            scope_id=source.scope_id,
            folder_id=source.folder_id,
            library_id=source.library_id,
            filename=_candidate_filename(source.filename, index, ts),
            image_bytes=image_bytes,
            mime_type=_COVER_MIME,
        )
        candidates.append(
            CoverCandidate(
                resource_id=str(row["id"]),
                timestamp_seconds=ts,
                width=width,
                height=height,
                filename=str(row.get("filename") or ""),
            )
        )

    if not candidates:
        raise CoverFrameError(
            status_code=422,
            detail="no usable frames could be extracted from this video",
        )

    return CoverCandidates(
        source_resource_id=str(source_resource_id),
        duration_seconds=frames.duration_seconds,
        candidates=tuple(candidates),
    )


async def _extract_frames_from_storage(file_path: str, num_frames: int):
    """``materialize`` 出本地路径 → ``extract_frames``，全程带上界。

    单独拆出来，是为了让"存储形状适配 + 超时 + 把 extract_frames 的软失败翻译
    成类型化失败"这三件事有一个可单测的落点。
    """
    from app.services.library.media_storage import materialize

    try:
        # 一个总上界罩住下载与抽帧两段。超时表现为 CancelledError 从 async
        # with 体内抛出 —— materialize 的 finally（删临时文件）与
        # extract_frames 的 TemporaryDirectory 照常执行，不留残留。
        async with asyncio.timeout(_TOTAL_DEADLINE_SECONDS):
            async with materialize(file_path) as local_path:
                if not local_path.exists():
                    raise CoverFrameError(
                        status_code=404, detail="source video file is missing on disk"
                    )
                result = await extract_frames(
                    str(local_path),
                    num_frames=num_frames,
                    frame_width=COVER_FRAME_WIDTH,
                    timeout_seconds=_EXTRACT_TIMEOUT_SECONDS,
                )
    except TimeoutError as exc:
        # asyncio.timeout 到点抛的是 TimeoutError（3.11+）。
        raise CoverFrameError(
            status_code=504,
            detail=f"frame extraction timed out after {_TOTAL_DEADLINE_SECONDS:.0f}s",
        ) from exc
    except ValueError as exc:  # materialize 的 containment guard
        raise CoverFrameError(
            status_code=400, detail="resource file_path escapes the download root"
        ) from exc
    except FileNotFoundError as exc:
        raise CoverFrameError(
            status_code=404, detail="source video file is missing on disk"
        ) from exc

    # extract_frames 的契约是"失败也不抛，返回空 attachments + error" —— 那是
    # 给聊天链路的优雅降级（没有画面也要把对话进行下去）。封面链路相反：抽不出
    # 帧就是这次操作失败，必须让用户看见原因，所以在这里把软失败翻译成硬失败。
    if result.error and not result.attachments:
        raise CoverFrameError(
            status_code=422, detail=f"frame extraction failed: {result.error}"
        )
    if not result.attachments:
        raise CoverFrameError(
            status_code=422, detail="frame extraction produced no frames"
        )
    return result


async def derive_cover_pair(
    *,
    frame_resource_id: str,
    user_id: str,
    repo: Optional[ResourceRepoProtocol] = None,
) -> CoverPair:
    """把选中的那一帧居中裁成 3:4 与 4:3 两张封面，各落成一个 resources 行。

    整段不碰视频也不碰 ffmpeg —— 候选帧已经是一个普通的图片 resource，所以这
    里直接走 canvas 既有的 derive 管线（``load_source_image`` →
    ``crop_normalized`` → ``persist_derived_image``）。这也是为什么对应的
    REST 端点是同步的：与 ``crop_derive_service`` 同理，单张图片裁切足够快，
    不值得让前端多绕一次 Realtime。

    Raises:
        CoverFrameError: 404/400 源帧不合法，422 裁切失败。
    """
    from app.repositories.resources_repository import ResourcesRepository
    from app.services.canvas.derive_persistence import DeriveError, load_source_image

    repo = repo or ResourcesRepository()

    try:
        source = await load_source_image(repo, frame_resource_id)
    except DeriveError as exc:
        # DeriveError 已经带对了状态码，只是换成本模块的类型，好让 router 有
        # 一个 except 分支而不是两个。
        raise CoverFrameError(status_code=exc.status_code, detail=exc.detail) from exc

    width, height = _image_size(source.file_bytes)
    stem = (source.filename.rsplit(".", 1)[0] or "cover")[:60]

    ids: dict[str, str] = {}
    for label, aspect in (
        ("vertical", COVER_VERTICAL_ASPECT),
        ("horizontal", COVER_HORIZONTAL_ASPECT),
    ):
        region = center_crop_region(width, height, aspect)
        try:
            cropped = crop_normalized(source.file_bytes, region, mime_type=_COVER_MIME)
        except CropError as exc:
            raise CoverFrameError(
                status_code=422, detail=f"{label} cover crop failed: {exc}"
            ) from exc
        row = await persist_derived_image(
            repo,
            user_id=user_id,
            scope_id=source.scope_id,
            folder_id=source.folder_id,
            library_id=source.library_id,
            filename=f"cover-{label}-{stem}.jpg",
            image_bytes=cropped,
            mime_type=_COVER_MIME,
        )
        ids[label] = str(row["id"])

    return CoverPair(
        vertical_resource_id=ids["vertical"],
        horizontal_resource_id=ids["horizontal"],
        source_frame_resource_id=str(frame_resource_id),
    )


__all__ = [
    "COVER_FRAME_WIDTH",
    "COVER_HORIZONTAL_ASPECT",
    "COVER_VERTICAL_ASPECT",
    "DEFAULT_COVER_FRAMES",
    "MAX_COVER_FRAMES",
    "CoverCandidate",
    "CoverCandidates",
    "CoverFrameError",
    "CoverPair",
    "SourceVideo",
    "center_crop_region",
    "derive_cover_pair",
    "extract_cover_candidates",
    "load_source_video",
]
