# app/services/media/transcode/hls_publisher.py

"""HLS 产物的发布与清理。从 TranscodeService 原样搬出。

关键不变量:master.m3u8 必须最后上传。播放器一旦拿到 master 就会立刻
去取各档 playlist 与分片,若 master 先到而分片还没传完,播放器会拿到
404 并放弃。原实现的做法是先把 master 移出目录树、put_dir 传完其余
文件、再单独 put_file 传 master、最后移回 —— 搬迁时完整保留。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

from loguru import logger

from app.core.config import settings
from app.services.library.media_storage import (
    hls_key,
    hls_key_prefix,
    library_store,
    to_file_path,
)
from app.services.media.transcode.transcode_service import TranscodeTier


class HlsPublisher:
    """把本地 HLS 目录发布到对象存储。"""

    async def publish(
        self,
        hls_dir: Path,
        base: Path,
        resource_id: str,
        version_id: str,
    ) -> str:
        """Make the freshly written HLS tree readable, return the ``hls_path``.

        Filesystem mode: ffmpeg already wrote to its final home, so this only
        computes the DOWNLOAD_PATH-relative path.

        Object-store mode: ffmpeg still writes locally (it needs random-access
        writes and an -hls_segment_filename pattern), then the tree is copied
        up and ``hls_path`` becomes ``sb://library/hls/{rid}/{vid}/master.m3u8``.

        **Upload order matters.** master.m3u8 goes up LAST, on its own, after
        every segment and per-tier playlist has landed. The fast path marks the
        version ``completed`` the moment phase 1 publishes, so a master that
        became visible before its segments would hand the player a playlist
        pointing at objects that do not exist yet. Uploading the master last
        makes the whole publish atomic from a reader's point of view: either
        the old master is there, or the new one plus everything it references.
        """
        master_rel = "master.m3u8"
        if not settings.HLS_OBJECT_STORE:
            return str((hls_dir / master_rel).relative_to(base))

        store = library_store()

        # Everything except the master, uploaded concurrently.
        def _key(rel: str) -> str:
            return hls_key(resource_id, version_id, rel)

        master_local = hls_dir / master_rel
        moved_master = None
        if master_local.exists():
            # Hold the master out of the batch by parking it outside the tree
            # walked by put_dir, then upload it explicitly afterwards.
            moved_master = hls_dir.parent / f".{hls_dir.name}.master.m3u8"
            shutil.move(str(master_local), str(moved_master))
        try:
            await store.put_dir(str(hls_dir), _key)
            if moved_master is not None:
                await store.put_file(
                    _key(master_rel), str(moved_master), "application/vnd.apple.mpegurl"
                )
        finally:
            if moved_master is not None and moved_master.exists():
                # Put it back so later phases (tier encode → master rewrite)
                # still see a complete local tree.
                shutil.move(str(moved_master), str(master_local))

        return to_file_path(store.bucket, _key(master_rel))

    async def clear(self, resource_id: str, version_id: str) -> None:
        """Drop a version's previously published HLS objects.

        The filesystem path uses ``shutil.rmtree``; the object store has no
        equivalent, and stale segments from a longer previous encode would
        otherwise linger forever (unreferenced by the new master, so harmless
        to playback, but leaking space indefinitely).
        """
        if not settings.HLS_OBJECT_STORE:
            return
        try:
            prefix = hls_key_prefix(resource_id, version_id)
            removed = await library_store().remove_prefix(prefix)
            if removed:
                logger.info(
                    f"[Transcode] cleared {removed} stale HLS objects: {prefix}"
                )
        except Exception as e:
            # Never fail a transcode over cleanup — worst case is leaked objects.
            logger.warning(f"[Transcode] HLS prefix cleanup failed (non-fatal): {e}")

    def write_master_playlist(
        self,
        hls_dir: Path,
        tiers: List[TranscodeTier],
        *,
        passthrough: bool = False,
        source_width: Optional[int] = None,
        source_height: Optional[int] = None,
        source_bitrate: Optional[int] = None,
    ) -> None:
        """Write the multi-bitrate master.m3u8 playlist."""
        lines = ["#EXTM3U"]
        for tier in tiers:
            bandwidth = tier.bitrate * 1000  # kbps → bps
            lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},"
                f"RESOLUTION={tier.width}x{tier.height},"
                f'NAME="{tier.name}"'
            )
            lines.append(f"{tier.name}/stream.m3u8")

        # Passthrough tier — original quality, highest bandwidth
        if passthrough and source_width and source_height:
            # Use probed bitrate or a generous fallback
            bw = source_bitrate if source_bitrate else 20_000_000
            lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={bw},"
                f"RESOLUTION={source_width}x{source_height},"
                f'NAME="Original"'
            )
            lines.append("source/stream.m3u8")

        master = hls_dir / "master.m3u8"
        # Atomic write: write to temp file then rename to prevent race with active readers
        tmp = master.with_suffix(".m3u8.tmp")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.rename(master)
        logger.info(f"Master playlist written: {master}")
