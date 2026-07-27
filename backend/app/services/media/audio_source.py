"""音频源路径解析的唯一入口。

合并前分散在四处,且两条链行为不一致:

- ``ai_transcription.py`` —— isabs → join(DOWNLOAD_PATH);带 isdir/size 守卫
- ``whisper_service.py``  —— exists → join → glob ``<stem>.*`` 兜底

``ai_transcription.py:263`` 的注释自己写着 "whisper_service has the same
resolution chain; mirror it here",但实际两边并不相同。本类取并集:解析
链带 glob 兜底,校验带 isdir/size 守卫。
"""

from __future__ import annotations

import glob
import os

from loguru import logger


class AudioSourceResolver:
    """把 DB 里存的 audio_path（通常是相对路径）解析成磁盘上的真实文件。"""

    def _download_root(self) -> str:
        from app.core.config import settings

        return settings.DOWNLOAD_PATH.rstrip("/")

    def resolve(self, audio_path: str) -> str:
        """返回绝对路径。三级链:as-is → join(DOWNLOAD_PATH) → glob 同名不同后缀。

        Raises:
            FileNotFoundError: 三级都没命中。
        """
        if os.path.exists(audio_path):
            return audio_path

        joined = os.path.join(self._download_root(), audio_path)
        if os.path.exists(joined):
            logger.info(f"Audio path {audio_path} relative; resolved to {joined}")
            return joined

        # downloader 可能存成 audio.mp3 而 DB 记的是 audio.m4a（或反之）
        parent = os.path.dirname(joined) or "."
        stem = os.path.basename(audio_path).rsplit(".", 1)[0]
        candidates = sorted(glob.glob(os.path.join(parent, f"{stem}.*")))
        if candidates:
            logger.info(f"Audio path {audio_path} matched by glob: {candidates[0]}")
            return candidates[0]

        raise FileNotFoundError(f"audio file not found: {audio_path}")

    def assert_playable(self, audio_path: str) -> str:
        """校验解析结果是非空文件,返回**原始入参**（调用方要原样存回 DB）。

        用 isfile 而非 exists:图集若从未下载背景音乐,路径会回退成一个
        目录,exists() 放行后 ASR provider 才炸,报的是不可诊断的
        "Invalid audio URI"。这里 fast-fail 并给出可操作的信息。
        """
        try:
            full_path = self.resolve(audio_path)
        except FileNotFoundError:
            raise RuntimeError(
                f"audio file missing or empty at dispatch time: {audio_path}"
            )

        try:
            if os.path.isdir(full_path):
                raise RuntimeError(
                    f"audio path is a directory (gallery without downloaded "
                    f"music?): {audio_path}"
                )
            if os.path.isfile(full_path) and os.path.getsize(full_path) > 0:
                return audio_path
        except OSError:
            pass
        raise RuntimeError(f"audio file missing or empty at dispatch time: {audio_path}")

    def to_relative(self, abs_path: str) -> str:
        """去掉 DOWNLOAD_PATH 前缀 —— /media 路由按相对路径提供文件。"""
        root = self._download_root()
        if abs_path.startswith(root + "/"):
            return abs_path[len(root) + 1 :]
        return abs_path
