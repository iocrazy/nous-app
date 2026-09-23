"""回收 CLI 类 provider 写产物的临时目录（H1）。

CLI provider（jimeng-cli / codex）与 nous-engine 放大桥不返回 URL，而是把文件写进自己
``tempfile.mkdtemp(prefix=…)`` 出来的临时目录再把路径交回来。Tier-1
``register_generated_media(source_path=…)`` 把它拷进存储之后，那个目录就没人
要了——不收的话 worker 的 /tmp 会无界增长。

这里刻意只认**父目录 basename 的前缀**：一个非临时目录的路径永远不可能触发
删除，所以「回收」不会退化成「删任意目录」。删除本身 ``ignore_errors=True``，
它是尽力而为的收尾，绝不该把一次已经成功的生成判失败。
"""

from __future__ import annotations

import os
import shutil

# 与两个 provider 的 mkdtemp 前缀一一对应：
#   jimeng-cli → tempfile.mkdtemp(prefix="jimeng_")
#   codex CLI  → tempfile.mkdtemp(prefix="codeximg_")
#     (services/media/parsers/video_providers/codex_cli.py)
#   nous-engine → tempfile.mkdtemp(prefix="nousimg_")
#     (services/media/parsers/video_providers/nous_images.py)
SCRATCH_DIR_PREFIXES: tuple[str, ...] = ("jimeng_", "codeximg_", "nousimg_")


def reap_scratch_dir(local_path: str) -> None:
    """删掉持有 ``local_path`` 的 CLI 临时目录（前缀命中才删）。"""
    if not local_path:
        return
    parent = os.path.dirname(local_path)
    if not parent:
        return
    if os.path.basename(parent).startswith(SCRATCH_DIR_PREFIXES):
        shutil.rmtree(parent, ignore_errors=True)


__all__ = ["SCRATCH_DIR_PREFIXES", "reap_scratch_dir"]
