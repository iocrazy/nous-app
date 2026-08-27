"""import-from-resource 的 AVIF → PNG 转码（migration 441 配套）。

上游图片模型只收 jpeg/png/gif/webp（codex 原话），而素材库里第一个真实的封面
文件夹里**全是 AVIF**。转码放在"素材变参考图"的唯一入口，而不是各 provider。
"""

from __future__ import annotations

import os
import sys

import pytest
from PIL import Image, features

gm = (
    sys.modules["app.api.generated_media_router"]
    if "app.api.generated_media_router" in sys.modules
    else None
)
if gm is None:
    from app.main import app  # noqa: F401 — 让 __init__ 把模块名重绑

    gm = sys.modules["app.api.generated_media_router"]


@pytest.mark.skipif(not features.check("avif"), reason="Pillow 无 AVIF 支持")
def test_avif_is_transcoded_to_a_readable_png(tmp_path):
    src = tmp_path / "ref.avif"
    Image.new("RGB", (48, 64), (200, 30, 30)).save(src, format="AVIF")

    out = gm._transcode_to_png(str(src))
    try:
        with Image.open(out) as im:
            assert im.format == "PNG"
            assert im.size == (48, 64)
    finally:
        os.unlink(out)


def test_only_listed_formats_are_transcoded():
    # png/jpeg/webp 上游直接收，不该白转一遍。
    assert "image/png" not in gm._TRANSCODE_TO_PNG
    assert "image/jpeg" not in gm._TRANSCODE_TO_PNG
    assert "image/webp" not in gm._TRANSCODE_TO_PNG
    assert "image/avif" in gm._TRANSCODE_TO_PNG
