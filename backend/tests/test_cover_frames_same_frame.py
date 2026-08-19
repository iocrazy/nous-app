"""封面链路的核心保证：**用户挑中的那一帧，就是最终被裁的那一帧。**

候选帧不再落库（见 ``app/services/distribution/cover_frames.py`` 的模块
docstring），用户挑的是一个**时间点**，服务端事后回到源视频重抽。这换来了"素材
库不再被候选帧污染"，代价是引入了一条全新的失败模式：**重抽出来的可能不是同一
帧**。那种失败没有任何报错 —— 用户挑 A 得到 B，比素材库污染严重得多。

所以这里用两层守：

1. **命令同源**（``test_both_paths_build_the_same_ffmpeg_command``）——
   预览与重抽必须由同一个 builder 产出 argv。这条**不依赖 ffmpeg 二进制**，
   永远会跑；它把"两边参数漂移"（改了 seek 方式、加了 ``-accurate_seek``、
   把 ``-ss`` 挪到 ``-i`` 之后）这一整类退化挡在门外。
2. **真 ffmpeg 端到端**（``test_the_frame_the_user_picked_is_the_frame_that_gets_cropped``）
   —— 生成一段每帧都把自己的序号编成 8 位黑白色块的视频，跑完整链路，再从
   预览和最终封面里**把序号读回来**比对。断言的不是"没报错"，而是"两边读出的
   是同一个源帧号"。

第 2 条需要真 ffmpeg，因此带 skipif —— 而第 1 条不带，所以即使某天环境里没有
ffmpeg，这份文件也不会退化成"什么都没保证"。
"""

from __future__ import annotations

import base64
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image, ImageDraw

from app.services.canvas.image_crop import CropRegion
from app.services.distribution import cover_frames as cf
from app.services.media.render import video_frame_extractor as vfx

pytestmark = pytest.mark.unit

FPS = 24
FRAME_COUNT = 240
VIDEO_W, VIDEO_H = 640, 360

# 帧号色块条在画面里的归一化位置（8 个块横向排开）。
#
# ⚠️ 位置不是随手挑的：两张封面都是**居中裁**，16:9 源裁 3:4 只留中间 42% 的
# 宽度、裁 4:3 只留中间 42% 的高度。色块条铺满整幅画面时会被裁掉大半，读回来
# 的是别的块 —— 第一版就是这么红的（读出 63，应为 156）。所以把它收进中间
# 30%~70% 的窗口里，两种裁切都能完整保住。
MARK_X0, MARK_X1 = 0.30, 0.70
MARK_Y0, MARK_Y1 = 0.40, 0.60


# ============================================================
# 1. 命令同源 —— 不需要 ffmpeg，永远会跑
# ============================================================


async def _argv_from_extract_frames(ts: float, width: int) -> List[str]:
    """跑 ``extract_frames``、截下它为时间点 ``ts`` 真正执行的 argv。

    打桩打在 ``create_subprocess_exec`` 而不是更上层，是为了让被观察的就是"真
    的会被 exec 出去的那串参数"——那才是决定解出哪一帧的东西。
    """
    seen: List[List[str]] = []

    async def fake_exec(*args, **kwargs):
        proc = AsyncMock()
        if "ffprobe" in args[0]:
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"10.0\n", b""))
            return proc
        seen.append(list(args))
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"stub"))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        with patch.object(vfx.shutil, "which", lambda b: f"/usr/bin/{b}"):
            with patch.object(vfx.Path, "exists", lambda self: True):
                with patch.object(vfx.Path, "is_file", lambda self: True):
                    await vfx.extract_frames(
                        "/tmp/does-not-matter.mp4",
                        num_frames=1,
                        frame_width=width,
                    )
    assert seen, "extract_frames 没有 exec 过 ffmpeg —— 这条测试什么都没观察到"
    return seen[0]


async def _argv_from_extract_frame_at(ts: float, width: int) -> List[str]:
    seen: List[List[str]] = []

    async def fake_exec(*args, **kwargs):
        seen.append(list(args))
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"stub"))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        with patch.object(vfx.shutil, "which", lambda b: f"/usr/bin/{b}"):
            with patch.object(vfx.Path, "exists", lambda self: True):
                with patch.object(vfx.Path, "is_file", lambda self: True):
                    await vfx.extract_frame_at(
                        "/tmp/does-not-matter.mp4",
                        timestamp_seconds=ts,
                        frame_width=width,
                    )
    assert seen, "extract_frame_at 没有 exec 过 ffmpeg —— 这条测试什么都没观察到"
    return seen[0]


def _without_output_path(argv: List[str]) -> List[str]:
    """输出路径必然不同（各自的临时目录）；其余每一个 token 都必须一致。"""
    return argv[:-1]


async def test_both_paths_build_the_same_ffmpeg_command() -> None:
    """预览与重抽必须是**同一条** ffmpeg 命令，只有输出路径不同。

    这是"挑 A 得到 B"最隐蔽的成因：两边各写一份 argv，某天只有一边加了
    ``-accurate_seek`` 或把 ``-ss`` 挪到 ``-i`` 之后，于是它们开始落在不同的
    帧上 —— 没有任何报错，用户只会觉得"封面怎么不是我选的那张"。

    ``extract_frames`` 的采样点由它自己算（时长 10s、1 帧 → 中点 5.0s），所以
    这里拿它实际用的那个秒数去喂 ``extract_frame_at``，比较的才是同一个坐标。
    """
    width = cf.COVER_PREVIEW_WIDTH
    batch = await _argv_from_extract_frames(5.0, width)

    # 从 batch argv 里把它真正 seek 的秒数读出来 —— 不假设它等于我们传的值。
    ts_used = float(batch[batch.index("-ss") + 1])
    single = await _argv_from_extract_frame_at(ts_used, width)

    assert _without_output_path(batch) == _without_output_path(single)
    # 正向自查：比较的确实是一条真命令，而不是两个空列表。
    assert batch[0] == "ffmpeg"
    assert "-ss" in batch and "-frames:v" in batch


@pytest.mark.parametrize("width", [cf.COVER_PREVIEW_WIDTH, cf.COVER_FRAME_WIDTH])
async def test_the_builder_is_the_only_place_the_seek_is_spelled(width: int) -> None:
    """两条路径都必须**经过** ``seek_frame_cmd``，而不是各自拼一份长得一样的
    argv —— 长得一样只是此刻一样，经过同一个函数才是永远一样。"""
    ts = 3.75
    expected = vfx.seek_frame_cmd(
        "/tmp/does-not-matter.mp4", ts, "OUT", frame_width=width
    )
    single = await _argv_from_extract_frame_at(ts, width)
    assert _without_output_path(single) == _without_output_path(expected)
    assert f"scale={width}:-1" in single


# ============================================================
# 2. 真 ffmpeg 端到端 —— 读回帧号比对
# ============================================================

_HAVE_FFMPEG = (
    shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
)


def _encode_indexed_video(dst_dir: Path) -> Path:
    """生成一段视频，每帧把自己的序号编成 8 个黑白色块。

    为什么不用纯色/文字：JPEG 有损、缩放会重采样，颜色差一两级就分不清，文字
    又要 OCR。大色块的 0/1 在任何压缩与缩放下都稳，读回来是**精确**的帧号 ——
    这条测试要证的正是"精确到同一帧"，判据不能是模糊的。

    刻意用稀疏关键帧（``-g 240``，整段只有一个 I 帧）+ B 帧：这是 seek 最难的
    情形，"随便怎样都对"的编码证明不了什么。
    """
    frames = dst_dir / "png"
    frames.mkdir(parents=True, exist_ok=True)
    step = (MARK_X1 - MARK_X0) / 8
    for i in range(FRAME_COUNT):
        img = Image.new("RGB", (VIDEO_W, VIDEO_H), (128, 64, 32))
        draw = ImageDraw.Draw(img)
        for bit in range(8):
            left = (MARK_X0 + bit * step) * VIDEO_W
            draw.rectangle(
                [
                    left + 2,
                    MARK_Y0 * VIDEO_H,
                    left + step * VIDEO_W - 3,
                    MARK_Y1 * VIDEO_H,
                ],
                fill=(255, 255, 255) if (i >> bit) & 1 else (0, 0, 0),
            )
        img.save(frames / f"f_{i:04d}.png")

    out = dst_dir / "indexed.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            str(FPS),
            "-i",
            str(frames / "f_%04d.png"),
            "-c:v",
            "libx264",
            "-crf",
            "14",
            "-g",
            str(FRAME_COUNT),
            "-bf",
            "3",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    return out


_FULL_FRAME = CropRegion(x=0.0, y=0.0, width=1.0, height=1.0)


def _read_frame_index(image_bytes: bytes, region: CropRegion = _FULL_FRAME) -> int:
    """把 8 个色块读回成帧号。与缩放、JPEG 质量无关。

    ``region`` 说明这张图是原帧的哪一块（整幅就是默认值）—— 裁切之后色块条在
    归一化坐标里的位置会变，得把原坐标映射过去再采样。用生产的 ``CropRegion``
    表达而不是把偏移量硬编码进来，是为了让"改了裁切策略"这件事在这里表现为一
    条真实的红，而不是一堆对不上的魔数。
    """
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    step = (MARK_X1 - MARK_X0) / 8
    y_in_crop = (0.5 - region.y) / region.height
    bits = 0
    for bit in range(8):
        x_orig = MARK_X0 + (bit + 0.5) * step
        x_in_crop = (x_orig - region.x) / region.width
        assert 0.0 <= x_in_crop <= 1.0 and 0.0 <= y_in_crop <= 1.0, (
            f"色块 {bit} 落在裁切区之外（{x_in_crop:.3f}, {y_in_crop:.3f}）—— "
            f"这张图读不出帧号，比对无从谈起"
        )
        px = min(w - 1, int(x_in_crop * w))
        py = min(h - 1, int(y_in_crop * h))
        r, g, b = img.getpixel((px, py))
        if (r + g + b) / 3 > 128:
            bits |= 1 << bit
    return bits


class _FsRepo:
    """文件系统形状的最小 repo —— 让 materialize 直接拿到真实路径（不走对象
    存储），这样测试里跑的是真 ffmpeg 而不是一层 mock。"""

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self.created: List[Dict[str, Any]] = []
        self.updated: List[Dict[str, Any]] = []
        self.versions: List[Dict[str, Any]] = []
        self.items: List[Dict[str, Any]] = []

    async def get_resource_by_id(self, resource_id: str) -> Optional[Dict[str, Any]]:
        if str(resource_id) != "500":
            return None
        return {
            "id": "500",
            "file_path": self.rel_path,
            "file_type": "video",
            "mime_type": "video/mp4",
            "filename": "indexed.mp4",
        }

    async def get_first_resource_item(self, resource_id: str) -> Dict[str, Any]:
        return {"scope_id": "1", "folder_id": None, "library_id": None}

    async def create_resource(self, data: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(data)
        row["id"] = f"7770000000000{len(self.created):03d}"
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


@pytest.mark.skipif(
    not _HAVE_FFMPEG,
    reason="needs a real ffmpeg/ffprobe — the point of this test is the real seek",
)
async def test_the_frame_the_user_picked_is_the_frame_that_gets_cropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端：预览里看到的帧号，必须等于最终封面里的帧号。

    流程和用户走的一模一样：抽帧拿候选（240 宽的预览）→ 挑一个 → 按它的时间点
    重抽并裁切（1080 宽）→ 从落盘的竖版封面里把帧号读回来。

    ⚠️ 断言是**肯定式**的（两个具体帧号相等），不是"没有抛异常"。退化时（时间
    点被丢、被取整、重抽落到别的帧）这条会直接红，而不是空洞地通过。
    """
    from app.core.config import settings
    from app.services.canvas import derive_persistence

    root = tmp_path / "downloads"
    root.mkdir()
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(root))
    monkeypatch.setattr(settings, "MEDIA_S3_CACHE_DIR", "")

    async def _off() -> bool:
        return False

    monkeypatch.setattr(derive_persistence, "unified_storage_enabled", _off)

    video = _encode_indexed_video(tmp_path / "src")
    rel = "videos/indexed.mp4"
    (root / "videos").mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(video.read_bytes())

    repo = _FsRepo(rel)

    # ── 用户点"抽帧" ────────────────────────────────────────────────
    out = await cf.extract_cover_candidates(
        source_resource_id="500", num_frames=4, repo=repo
    )
    assert len(out.candidates) == 4, "真 ffmpeg 没抽出候选 —— 后面的比对无从谈起"
    assert repo.created == [], "抽帧仍然一行库都不该写"

    # 用户看到的每一张预览，都能读出一个确定的帧号。
    previewed: Dict[int, int] = {}
    for candidate in out.candidates:
        raw = base64.b64decode(candidate.preview_data_url.split(",", 1)[1])
        previewed[candidate.index] = _read_frame_index(raw)
    # 前提自查：四个候选必须是四张**不同**的帧，否则"挑对了"是碰巧。
    assert len(set(previewed.values())) == 4, previewed

    # ── 用户挑第 3 张 ───────────────────────────────────────────────
    picked = out.candidates[2]
    pair = await cf.derive_cover_pair(
        source_resource_id="500",
        timestamp_seconds=picked.timestamp_seconds,
        user_id="00000000-0000-0000-0000-000000000001",
        repo=repo,
    )
    assert pair.vertical_resource_id and pair.horizontal_resource_id
    assert len(repo.created) == 2, "选帧只该落两张成品封面"

    # ── 落盘的**两张**封面里都读回帧号，与预览比对 ──────────────────
    #
    # 源是 16:9，所以竖版裁两侧、横版裁上下 —— 两种裁法各自会破坏不同方向的
    # 信息。两张都比，才不会出现"竖版对了、横版取错帧"这种半对的退化。
    frame_w, frame_h = Image.open(
        BytesIO(
            await vfx.extract_frame_at(
                str(root / rel),
                timestamp_seconds=picked.timestamp_seconds,
                frame_width=cf.COVER_FRAME_WIDTH,
            )
        )
    ).size
    for label, aspect in (
        ("vertical", cf.COVER_VERTICAL_ASPECT),
        ("horizontal", cf.COVER_HORIZONTAL_ASPECT),
    ):
        row = next(
            r for r in repo.updated if r["filename"].startswith(f"cover-{label}-")
        )
        cropped = (root / row["file_path"]).read_bytes()
        region = cf.center_crop_region(frame_w, frame_h, aspect)
        assert (
            _read_frame_index(cropped, region) == previewed[picked.index]
        ), f"{label} 封面裁的不是用户挑的那一帧"


@pytest.mark.skipif(
    not _HAVE_FFMPEG,
    reason="needs a real ffmpeg/ffprobe — the point of this test is the real seek",
)
async def test_re_reading_the_same_second_twice_gives_the_same_frame(
    tmp_path: Path,
) -> None:
    """重抽必须是确定性的 —— 同一个秒数，隔多久重来都是同一帧。

    这是整个设计赖以成立的前提：候选帧不落库，"那一帧"只由 (文件, 秒数) 定义。
    如果 ffmpeg 在稀疏关键帧 + B 帧的源上对同一时间点给出不同结果，用户挑完
    封面等一会儿再点，就会拿到另一张。
    """
    video = _encode_indexed_video(tmp_path / "src")

    for ts in (0.5, 4.1, 9.5):
        first = await vfx.extract_frame_at(
            str(video), timestamp_seconds=ts, frame_width=cf.COVER_PREVIEW_WIDTH
        )
        second = await vfx.extract_frame_at(
            str(video), timestamp_seconds=ts, frame_width=cf.COVER_PREVIEW_WIDTH
        )
        big = await vfx.extract_frame_at(
            str(video), timestamp_seconds=ts, frame_width=cf.COVER_FRAME_WIDTH
        )
        assert first and second and big, f"{ts}s 抽不出帧"
        # 同宽度两次 → 逐字节相同。
        assert first == second
        # 不同宽度 → 尺寸不同，但**源帧相同**（预览与裁切正是这两种宽度）。
        assert _read_frame_index(first) == _read_frame_index(big)
