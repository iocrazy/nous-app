"""Distribution — 封面抽帧（从已选视频里均匀取帧，让用户挑一张做封面）。

发布模块的 ``publish_tasks.cover_vertical_resource_id`` /
``cover_horizontal_resource_id`` 从 migration 356 起就存在，浏览器侧的
``_set_cover`` 也早就实现了，唯独缺"封面从哪来"。这个模块补的就是那一段。

分两步，因为两步的代价差了两个数量级：

    抽帧（慢，DBOS workflow）   materialize 视频 → ffmpeg 均匀取 N 帧
                               → **只**返回小预览图 + 各自的时间点（不落库）
    选帧（快，同步 REST）       用户挑一个时间点 → 按秒数重抽那一帧（全尺寸）
                               → 居中裁出 3:4 / 4:3 两张 → 两个 resources 行
                               → 写回 publish_tasks

候选帧为什么不落库（2026-08-18 改）
==================================
候选帧原本每一张都落成一个 ``source_type='derived'`` 的 resources 行，继承源
视频的 ``folder_id`` / ``library_id`` / ``scope_id`` —— 于是它们出现在用户放那
个视频的**同一个文件夹里**，跟正经素材混在一起。而 ``source_type`` 与画布派生
图完全同值，前端没有任何字段能把两者分开，唯一痕迹是文件名前缀（按文件名猜身
份是脆弱的：用户自己上传同前缀文件就会被误判）。

现在候选帧是**纯临时物**：预览图以 base64 随 ``task_tracking.metadata`` 送达
前端，用户挑中之后由 ``derive_cover_pair`` 按 ``timestamp_seconds`` 重抽那一
帧。存储零负担，也没有任何需要回收的临时区。

⚠️ 这条设计的前提是"同一秒数重抽出来的就是同一帧"，否则用户会**挑 A 得到 B**
—— 比素材库污染严重得多。该前提已实测而不是推断：``-ss`` 在 ``-i`` 之前是
ffmpeg 的精确输入 seek，跨 mp4 / mkv / mov / webm、稀疏关键帧 + B 帧、VFR 源，
同一时间点三次抽帧产出**逐字节相同**的 JPEG，且 240 / 1080 / 原生三种宽度解出
的是**同一源帧**（预览与最终裁切因此必然同帧）。命令由
``video_frame_extractor.seek_frame_cmd`` 单点构造，两条路径共用同一个 builder，
让"两边命令漂移"这一类失败从"不太可能"变成"不可能"。

成品封面（``cover-vertical-*`` / ``cover-horizontal-*``）**仍然是真 resources
行**，这是对的：``publish_tasks.cover_*_resource_id`` 按 id 引用它们，浏览器
侧 ``_set_cover`` 要真去取那个文件。

三层复用，本模块不自己实现任何一层
==================================
1. ``media_storage.materialize()`` —— 素材在对象存储，而 ffmpeg 要本地路径。
   它同时是"两种 file_path 形状的适配器"（``sb://`` 流式落盘 / 文件系统直接
   给真实路径）、containment guard（``..`` 逃不出 DOWNLOAD_PATH）、以及本机
   NVMe 的 LRU 读缓存。**没有走 ``get_resource_media_url()`` + HTTP 下载**：
   那条路是给"对端 API 主动来拉"设计的（抖音发布、volcengine ASR），我们自己
   要读同一个进程能直接读到的文件，绕一圈签名 URL 只会多一次网关往返、丢掉
   磁盘缓存，还得自己管临时文件。
2. ``video_frame_extractor.extract_frames()`` / ``.extract_frame_at()`` ——
   前者均匀采样出候选预览，后者按秒数重抽单帧；两者共用
   ``seek_frame_cmd`` 构造 ffmpeg 命令，所以"预览看到的那一帧"与"裁切用的
   那一帧"同源。ffmpeg 失败时都优雅降级成 ``result.error`` / ``None``
   而不是抛异常。
3. ``canvas.image_crop.crop_normalized()`` + ``derive_persistence
   .persist_derived_image()`` —— 裁切与"bytes → resources 行"落地。只有成品
   封面走这一层，候选帧不走。

临时文件（§7.6：不该留在磁盘上的东西一件都不留）
==============================================
**本模块一个临时文件都不创建**，所以"成功 / 失败 / 异常"三条路径的清理不是
这里新写的守则，而是三个被复用组件各自已经拥有的：

    materialize()            finally: Path(tmp).unlink(missing_ok=True)
    extract_frames()         with tempfile.TemporaryDirectory()（删目录而非
    extract_frame_at()       逐个文件，部分失败也不留残留）
    persist_derived_image()  finally: Path(tmp_name).unlink(missing_ok=True)

唯一要保证的是**异常/超时路径也能走到那三个 finally**：抽帧与重抽都包在
``asyncio.timeout`` 里而不是自己写循环等待，超时表现为 CancelledError 从
``async with`` 内部抛出，两个上下文管理器的 ``finally`` 照常执行。
``tests/test_cover_frames.py`` 对三条路径各有一条断言。

裁切策略见 ``center_crop_region`` 的 docstring。
"""

from __future__ import annotations

import asyncio
import base64
import math
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

from loguru import logger

from app.services.canvas.derive_persistence import (
    ResourceRepoProtocol,
    persist_derived_image,
)
from app.services.canvas.image_crop import CropError, CropRegion, crop_normalized
from app.services.library.resource_file_path import resolve_resource_file_path
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

# 候选预览图的采样宽度。前端候选条里每张的 CSS 尺寸是 62×82（见
# distribution-v4.css 的 ``.cover-cand``），240 宽在 3 倍屏上仍有富余。预览
# **只**用来给人挑，真正的裁切走 COVER_FRAME_WIDTH 重抽，所以这里小是纯收益。
COVER_PREVIEW_WIDTH = 240

# 单张预览的字节上界。预览随 ``task_tracking.metadata`` 走 Supabase Realtime
# 推给订阅者，整行超限会被丢弃（"完成了但一张候选都没有"），所以总量必须**由
# 构造保证**而不是靠"通常不会很大"：MAX_COVER_FRAMES × 该上界 = 12 × 14 KB
# ≈ 168 KB 原始 / ≈ 224 KB base64，离 1 MB 的行上限有数倍余量。
# 实测：240 宽的普通画面 q75 约 8 KB，只有高噪点画面才需要 _fit_preview 降级。
_PREVIEW_MAX_BYTES = 14 * 1024
# _fit_preview 的收缩下界。低于它就不再缩，宁可略微超预算也要给出一张能看的
# 图 —— 但实际到不了：64 宽的 JPEG 连纯噪声都远小于上界。
_PREVIEW_MIN_WIDTH = 64

# 候选帧数。抽帧要下载整个源视频并跑 N 次 ffmpeg seek，12 张已经远超"挑一张
# 封面"的需要；上限同时也是上面那条 metadata 预算的乘数。
DEFAULT_COVER_FRAMES = 6
MAX_COVER_FRAMES = 12

# 两道上界（§7.2：所有等待必须有上界）。
# ffmpeg 侧的上界由 extract_frames 的 timeout_seconds 承担，它会再按帧数均分。
_EXTRACT_TIMEOUT_SECONDS = 180.0
# 外层总上界要把 materialize 的下载也罩进去 —— 那一步本身没有超时，几个 GB
# 的源视频遇上存储抖动就是无限期挂起。10 分钟之后一律放弃。
_TOTAL_DEADLINE_SECONDS = 600.0

# 选帧（同步端点）的两道上界。这条路径现在也要 materialize + 一次 ffmpeg
# seek，不再只是裁一张已存在的图片 —— 抽帧刚跑完时源视频通常还在 materialize
# 的本机 NVMe 读通缓存里（命中即零下载），但缓存是 LRU 的，用户放着不动足够久
# 就会被挤掉，那时这一步要重新拉一次源文件。挂在 HTTP 请求上的等待必须有上
# 界，超时表现为 504 + "再试一次"，而不是网关替我们决定。
_SELECT_DEADLINE_SECONDS = 240.0
_SELECT_FFMPEG_TIMEOUT_SECONDS = 60.0

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
    """一个候选帧：一张小预览图 + 它在源视频里的时间点。

    **没有 resource_id，因为候选帧不落库**（见模块 docstring）。选帧时回传的
    是 ``timestamp_seconds``，服务端按它重抽同一帧。
    """

    index: int
    """在本次采样里的序号。给前端当稳定 key 用（时间点也唯一，但序号对"两帧
    恰好同秒"这种退化输入更稳）。"""
    timestamp_seconds: float
    """采样时间点，**必填**。它现在是选帧的唯一坐标，所以没有时间戳的帧根本
    不能成为候选 —— ``extract_frames`` 的 fps 兜底分支（ffprobe 读不到时长）
    只产帧不产时间戳，那种输出会被 ``extract_cover_candidates`` 判成类型化失
    败，而不是给用户一排点了没反应的图。"""
    preview_data_url: str
    """``data:image/jpeg;base64,...``，直接进 ``<img src>``。"""
    preview_width: int
    preview_height: int
    """预览图自身的像素尺寸 —— **不是**源视频的尺寸。真正参与裁切的是选帧时
    按 COVER_FRAME_WIDTH 重抽的那一张，它的尺寸在那一步现算。"""

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "timestamp_seconds": self.timestamp_seconds,
            "preview_data_url": self.preview_data_url,
            "preview_width": self.preview_width,
            "preview_height": self.preview_height,
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
    """产出这两张封面的那一帧从哪来。走时间点路径时是**源视频**的 id（帧本身
    没有 id），走旧的 frame-resource 路径时是那个帧 resource 的 id。"""


@dataclass(frozen=True)
class SourceVideo:
    """校验通过的源视频 + 它所在的 scope（决定成品封面落到哪）。"""

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
    source_width: int,
    source_height: int,
    target_aspect: float,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
) -> CropRegion:
    """求"能塞进源图的最大 target_aspect 矩形"，以 ``(focus_x, focus_y)`` 为锚点。

    返回归一化 ``[0,1]`` 区域，直接喂给 ``crop_normalized``。锚点默认 0.5/0.5 =
    居中；用户在工作室里拖动裁切框时传的是框中心的归一化坐标，窗口跟着它走，
    到边界就钳住（永远不会裁到图外）。

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

    2026-08-26 起锚点由用户决定：封面工作室的裁切框可以拖，框中心作为
    ``focus`` 传进来。居中仍是没人拖时的默认值，理由如上不变。显著性/人脸检测
    仍是自然升级位 —— 只是把"默认 0.5"换成检测值，签名不用再动。

    Raises:
        CoverFrameError: 源尺寸不合法或锚点越界（400）。
    """
    if not (0.0 <= focus_x <= 1.0 and 0.0 <= focus_y <= 1.0):
        raise CoverFrameError(
            status_code=400,
            detail=f"crop focus out of range: ({focus_x}, {focus_y})",
        )
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
        # 源比目标更宽 → 保满高度，窗口沿 x 跟着锚点走。
        width = target_aspect / source_aspect
        x = min(max(focus_x - width / 2.0, 0.0), 1.0 - width)
        return CropRegion(x=x, y=0.0, width=width, height=1.0)
    # 源比目标更高（或恰好相等）→ 保满宽度，窗口沿 y 跟着锚点走。
    height = min(source_aspect / target_aspect, 1.0)
    y = min(max(focus_y - height / 2.0, 0.0), 1.0 - height)
    return CropRegion(x=0.0, y=y, width=1.0, height=height)


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


def _fit_preview(image_bytes: bytes, max_bytes: int = _PREVIEW_MAX_BYTES) -> bytes:
    """把一张预览压到 ``max_bytes`` 以内，**保证终止**。

    为什么需要它：预览要经 ``task_tracking.metadata`` 走 Realtime，而超大行会
    被整条丢掉 —— 那种失败的样子是"任务完成了但一张候选都没有"，用户看不出发
    生了什么。所以总量不能靠"通常不会很大"，得由构造保证。

    先降 JPEG 质量再降分辨率（质量降级对 62×82 的缩略图几乎不可见，尺寸降级
    才会真的糊）。两层都是有界循环，且宽度每轮乘 0.75、下界
    ``_PREVIEW_MIN_WIDTH`` —— 一定会退出。

    压不动时返回**当前最小**的那一版而不是抛错：一张略微超预算的预览仍然能让
    用户挑封面，而抛错会把整次抽帧毁掉。实际到不了这一步（64 宽的纯噪声 JPEG
    也就几 KB），这里只是不留未定义行为。
    """
    if len(image_bytes) <= max_bytes:
        return image_bytes

    from PIL import Image

    try:
        with Image.open(BytesIO(image_bytes)) as opened:
            full = opened.convert("RGB")
        source_w, source_h = full.size
        width = source_w
        best = image_bytes
        while True:
            scaled = (
                full
                if width == source_w
                else full.resize((width, max(1, round(source_h * width / source_w))))
            )
            for quality in (70, 55, 40, 28):
                buf = BytesIO()
                scaled.save(buf, format="JPEG", quality=quality, optimize=True)
                candidate = buf.getvalue()
                if len(candidate) < len(best):
                    best = candidate
                if len(candidate) <= max_bytes:
                    return candidate
            if width <= _PREVIEW_MIN_WIDTH:
                return best
            width = max(_PREVIEW_MIN_WIDTH, int(width * 0.75))
    except Exception as exc:  # noqa: BLE001 — Pillow 的解码异常族很杂
        # 压不了就用原图：一张偏大的预览好过没有预览。总量仍受帧数上限约束。
        logger.warning(f"[cover_frames] preview shrink failed, using as-is: {exc!r}")
        return image_bytes


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
    # ⚠️ 不是 ``source.get("file_path")``。那一列对 ``source_type='web'`` 的行
    # （平台解析下载的素材）**按设计**为空 —— 共享下载字段只存 parsed_media
    # （PR-B）。生产里超过一半的视频是这种形状，直接读该列会把它们全判成"没有
    # 文件"，用户看到 400 "no file on disk yet"：跟修 scope 前的假 404 是同一
    # 类错误答案，只是换了个说法。阶梯与来源见 resource_file_path 的模块 docstring。
    file_path = await resolve_resource_file_path(source)
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


async def extract_cover_candidates(
    *,
    source_resource_id: str,
    num_frames: int = DEFAULT_COVER_FRAMES,
    repo: Optional[ResourceRepoProtocol] = None,
) -> CoverCandidates:
    """从源视频均匀抽 ``num_frames`` 帧，产出**预览图 + 时间点**，不落库。

    这个函数**一行 resources 都不写** —— 那正是它存在形态的重点（见模块
    docstring）。它读源视频、跑 ffmpeg、把结果打包返回；持久化只发生在用户真
    的挑中一帧之后，且只产出两张成品封面。

    没有 ``user_id`` 参数，因为没有任何东西归属到人。读源视频靠调用方建立的
    ambient tenant scope（HTTP 侧 request_scope，workflow 侧 request_scope）。

    Raises:
        CoverFrameError: 404/400 源不合法，422 抽不出可用帧 / 读不到时长，
            504 超时。
    """
    from app.repositories.resources_repository import ResourcesRepository

    repo = repo or ResourcesRepository()
    num_frames = max(1, min(MAX_COVER_FRAMES, num_frames))

    source = await load_source_video(repo, source_resource_id)
    frames = await _extract_frames_from_storage(source.file_path, num_frames)

    if not frames.sampled_at_seconds:
        # ffprobe 读不到时长 → extract_frames 走 fps 兜底，只产帧不产时间戳。
        # 那种输出对本链路没用：时间点是选帧的唯一坐标，没有它就无法重抽，
        # 展示出来的会是一排点了没反应的候选。说清楚为什么，而不是静默给空。
        raise CoverFrameError(
            status_code=422,
            detail=(
                "could not read this video's duration, so frames cannot be "
                "timestamped — pick a different video, or set the cover on "
                "the platform"
            ),
        )

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
        if index >= len(frames.sampled_at_seconds):
            # 上面的守卫保证了主分支（两个列表等长）；真走到这里说明
            # extract_frames 的契约变了，跳过比编一个时间点安全。
            logger.warning(f"[cover_frames] frame {index} has no timestamp; skipping")
            continue
        preview = _fit_preview(image_bytes)
        width, height = _image_size(preview)
        candidates.append(
            CoverCandidate(
                index=index,
                timestamp_seconds=float(frames.sampled_at_seconds[index]),
                preview_data_url=(
                    "data:image/jpeg;base64,"
                    + base64.b64encode(preview).decode("ascii")
                ),
                preview_width=width,
                preview_height=height,
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
                    # 预览宽度，不是裁切宽度。真正参与裁切的那一张在选帧时
                    # 按同一个时间点、按 COVER_FRAME_WIDTH 重抽 —— 实测两种
                    # 宽度解出的是同一源帧，所以"看到的"就是"用到的"。
                    frame_width=COVER_PREVIEW_WIDTH,
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


async def _reextract_frame_from_storage(
    file_path: str, timestamp_seconds: float
) -> bytes:
    """``materialize`` 出本地路径 → 在 ``timestamp_seconds`` 重抽一帧（全尺寸）。

    与 ``_extract_frames_from_storage`` 同构（同样的存储适配、同样把软失败翻
    译成类型化失败），区别只有三点：只抽一帧、用 COVER_FRAME_WIDTH、上界更短
    （这条挂在同步 HTTP 请求上，不是 workflow）。

    Raises:
        CoverFrameError: 404 文件不在，400 路径越界，422 抽不出这一帧，
            504 超时。
    """
    from app.services.library.media_storage import materialize
    from app.services.media.render.video_frame_extractor import extract_frame_at

    try:
        async with asyncio.timeout(_SELECT_DEADLINE_SECONDS):
            async with materialize(file_path) as local_path:
                if not local_path.exists():
                    raise CoverFrameError(
                        status_code=404, detail="source video file is missing on disk"
                    )
                frame = await extract_frame_at(
                    str(local_path),
                    timestamp_seconds=timestamp_seconds,
                    frame_width=COVER_FRAME_WIDTH,
                    timeout_seconds=_SELECT_FFMPEG_TIMEOUT_SECONDS,
                )
    except TimeoutError as exc:
        raise CoverFrameError(
            status_code=504,
            detail=(
                f"re-reading the chosen frame timed out after "
                f"{_SELECT_DEADLINE_SECONDS:.0f}s"
            ),
        ) from exc
    except ValueError as exc:  # materialize 的 containment guard
        raise CoverFrameError(
            status_code=400, detail="resource file_path escapes the download root"
        ) from exc
    except FileNotFoundError as exc:
        raise CoverFrameError(
            status_code=404, detail="source video file is missing on disk"
        ) from exc

    if not frame:
        # extract_frame_at 的契约是"失败返回 None 而不抛"。到这里说明 ffmpeg
        # 在那个时间点没能产出画面（时间点超出范围、文件被换过、解码失败）——
        # 必须说出来，不能给用户一个空白封面。
        raise CoverFrameError(
            status_code=422,
            detail=(
                f"could not re-read the frame at {timestamp_seconds:.1f}s — "
                "sample the video again and pick another frame"
            ),
        )
    return frame


async def _crop_and_persist_pair(
    repo: ResourceRepoProtocol,
    *,
    frame_bytes: bytes,
    user_id: str,
    scope_id: str,
    folder_id: Optional[str],
    library_id: Optional[str],
    stem: str,
    focus: tuple[float, float] = (0.5, 0.5),
) -> dict[str, str]:
    """一帧 → 竖版 3:4 + 横版 4:3，各落成一个 resources 行，返回两个 id。

    ``focus`` 是用户拖出来的裁切锚点（归一化，默认居中），两个画幅共用同一个。

    这两行**是**真素材：``publish_tasks.cover_*_resource_id`` 按 id 引用它们，
    浏览器侧发布时要真去取那个文件。归属跟随源视频的 scope / folder /
    library，``creator_id`` 是发起操作的人 —— ``persist_derived_image`` 的既有
    口径，这里没有另立一套。
    """
    width, height = _image_size(frame_bytes)
    ids: dict[str, str] = {}
    for label, aspect in (
        ("vertical", COVER_VERTICAL_ASPECT),
        ("horizontal", COVER_HORIZONTAL_ASPECT),
    ):
        region = center_crop_region(width, height, aspect, focus[0], focus[1])
        try:
            cropped = crop_normalized(frame_bytes, region, mime_type=_COVER_MIME)
        except CropError as exc:
            raise CoverFrameError(
                status_code=422, detail=f"{label} cover crop failed: {exc}"
            ) from exc
        row = await persist_derived_image(
            repo,
            user_id=user_id,
            scope_id=scope_id,
            folder_id=folder_id,
            library_id=library_id,
            filename=f"cover-{label}-{stem}.jpg",
            image_bytes=cropped,
            mime_type=_COVER_MIME,
        )
        ids[label] = str(row["id"])
    return ids


async def derive_cover_pair(
    *,
    source_resource_id: str,
    timestamp_seconds: float,
    user_id: str,
    repo: Optional[ResourceRepoProtocol] = None,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
) -> CoverPair:
    """按时间点重抽那一帧，以 ``(focus_x, focus_y)`` 为锚点裁成 3:4 与 4:3 两张封面。

    用户挑的是**一个时间点**，不是一个已存在的图片 —— 候选帧从不落库。所以这
    一步要回到源视频、在同一秒重抽一次（这次按 COVER_FRAME_WIDTH，全尺寸）。

    ⚠️ "看到的就是用到的"靠两件事保证，两件都不是推断：
    1. 预览与这一次用的是**同一个 ffmpeg 命令构造器**
       （``video_frame_extractor.seek_frame_cmd``），所以不存在参数漂移；
    2. 同一时间点重抽产出同一源帧 —— 实测跨容器/编码/VFR 逐字节稳定，且
       240 / 1080 / 原生三种宽度解出同一帧。

    Raises:
        CoverFrameError: 404/400 源视频不合法，422 重抽不出这一帧 / 裁切失败，
            504 超时。
    """
    from app.repositories.resources_repository import ResourcesRepository

    repo = repo or ResourcesRepository()

    # 时间点是用户可达的输入（前端把候选里的数字原样回传），所以畸形值是一条
    # **正常路径**而不是内部不变量：NaN / inf 会一路飘到 ffmpeg 的 -ss 变成一
    # 个 500。给它一个说得清的 400。
    if not math.isfinite(timestamp_seconds) or timestamp_seconds < 0:
        raise CoverFrameError(
            status_code=400,
            detail=f"frame timestamp out of range: {timestamp_seconds}",
        )

    source = await load_source_video(repo, source_resource_id)
    frame_bytes = await _reextract_frame_from_storage(
        source.file_path, float(timestamp_seconds)
    )
    stem = (source.filename.rsplit(".", 1)[0] or "cover")[:60]
    ids = await _crop_and_persist_pair(
        repo,
        frame_bytes=frame_bytes,
        user_id=user_id,
        scope_id=source.scope_id,
        folder_id=source.folder_id,
        library_id=source.library_id,
        stem=stem,
        focus=(focus_x, focus_y),
    )
    return CoverPair(
        vertical_resource_id=ids["vertical"],
        horizontal_resource_id=ids["horizontal"],
        source_frame_resource_id=str(source_resource_id),
    )


@dataclass(frozen=True)
class GrabbedFrame:
    """A frame the user grabbed by hand, already durable enough to be a reference.

    ``generated_media_id`` and ``url`` are the SAME row seen two ways; both are
    returned because the caller needs both (the id to dedupe the pool, the URL
    to render it and to hand to the model).
    """

    generated_media_id: str
    url: str
    timestamp_seconds: float

    def as_dict(self) -> dict:
        return {
            "generated_media_id": self.generated_media_id,
            "url": self.url,
            "timestamp_seconds": self.timestamp_seconds,
        }


async def grab_frame_as_reference(
    *,
    source_resource_id: str,
    timestamp_seconds: float,
    user_id: str,
    repo: Optional[ResourceRepoProtocol] = None,
) -> GrabbedFrame:
    """抓一帧当**参考图**用（封面工作室），而不是当成品封面。

    与 ``derive_cover_pair`` 的区别只有落点，抽帧那一半完全共用：

        derive_cover_pair       重抽 → 居中裁 3:4 + 4:3 → 两个 resources 行
        grab_frame_as_reference 重抽 →   不裁          → 一个 generated_media 行

    ⚠️ 落点不是随便选的。出图链路的参考图入口只认
    ``/api/v1/generated-media/{id}/(cover|stream|file)``；别的 URL 会被
    ``generated_media_local_path()`` 返回 None 然后**静默丢弃**。落成 resources
    行的帧看起来一切正常，却永远进不了模型 —— 而且不报错。

    **不裁**也是刻意的：这一帧是说"我的片子里有什么"（人物长相、场景），裁掉
    边缘只会丢信息；构图由模型按 3:4 重新生成，不由这张参考图决定。

    Raises:
        CoverFrameError: 404/400 源视频不合法，422 重抽不出这一帧，504 超时。
    """
    import os
    import tempfile

    from app.repositories.resources_repository import ResourcesRepository
    from app.services.library.generated_media_service import (
        GenerationOrigin,
        register_generated_media,
    )
    from app.services.library.resources_service import _resolve_personal_team_id

    repo = repo or ResourcesRepository()

    # 与 derive_cover_pair 同源的校验：时间点是用户可达输入，NaN / inf 会一路飘
    # 到 ffmpeg 的 -ss 变成 500。
    if not math.isfinite(timestamp_seconds) or timestamp_seconds < 0:
        raise CoverFrameError(
            status_code=400,
            detail=f"frame timestamp out of range: {timestamp_seconds}",
        )

    source = await load_source_video(repo, source_resource_id)
    frame_bytes = await _reextract_frame_from_storage(
        source.file_path, float(timestamp_seconds)
    )

    scope_id = int(await _resolve_personal_team_id(str(user_id)))
    tmp_path: Optional[str] = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="cover_frame_", suffix=".jpg")
        with os.fdopen(fd, "wb") as fh:
            fh.write(frame_bytes)
        row = await register_generated_media(
            user_id=str(user_id),
            scope_id=scope_id,
            source_path=tmp_path,
            mime=_COVER_MIME,
            origin=GenerationOrigin(
                kind="canvas_upload",
                params={
                    "filename": f"frame-{timestamp_seconds:.1f}s.jpg",
                    # 溯源：哪个视频、第几秒。存这两个是为了将来能回答"这张参考
                    # 图是从哪来的"，而不必靠文件名去猜。
                    "cover_frame_source_resource_id": str(source_resource_id),
                    "cover_frame_timestamp_seconds": float(timestamp_seconds),
                },
            ),
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    gen_id = row.get("id")
    if gen_id is None:
        # register_generated_media 的成功路径一定有 id；到这里说明写入没成交。
        # 回一个类型化失败，而不是让 None 顺着 f-string 变成字面量 "None" 的 URL。
        raise CoverFrameError(
            status_code=500, detail="grabbed frame could not be stored"
        )
    return GrabbedFrame(
        generated_media_id=str(gen_id),
        url=f"/api/v1/generated-media/{gen_id}/cover",
        timestamp_seconds=float(timestamp_seconds),
    )


async def derive_cover_pair_from_frame(
    *,
    frame_resource_id: str,
    user_id: str,
    repo: Optional[ResourceRepoProtocol] = None,
) -> CoverPair:
    """旧路径：从一个**已落库的**帧 resource 裁出两张封面。

    ⚠️ 保留它只为一个理由：**部署错峰**。前端（Cloudflare Pages）与后端
    （gpupc）是两条独立的发布链，同一次合并谁先上线不确定，而用户可能正好卡在
    "候选帧已经拿到、还没点选"的中间。旧前端手里的候选是 resource id，新后端
    如果只认时间点就会把那一次点击变成 422。这条分支让那个窗口不产生错误。

    它本身**不写候选帧**，只是读一个已经存在的图片 resource 再裁，所以留着它
    不会重新制造素材库污染。窗口过去（下一次前端发布之后）可以删。

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

    stem = (source.filename.rsplit(".", 1)[0] or "cover")[:60]
    ids = await _crop_and_persist_pair(
        repo,
        frame_bytes=source.file_bytes,
        user_id=user_id,
        scope_id=source.scope_id,
        folder_id=source.folder_id,
        library_id=source.library_id,
        stem=stem,
    )
    return CoverPair(
        vertical_resource_id=ids["vertical"],
        horizontal_resource_id=ids["horizontal"],
        source_frame_resource_id=str(frame_resource_id),
    )


__all__ = [
    "COVER_FRAME_WIDTH",
    "COVER_HORIZONTAL_ASPECT",
    "COVER_PREVIEW_WIDTH",
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
    "derive_cover_pair_from_frame",
    "extract_cover_candidates",
    "load_source_video",
]
