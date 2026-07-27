"""缩略图 / sprite 写侧落位的收拢点（尚非全量唯一入口，见下）。

当前规则:派生产物一律留在文件系统,只有原件进对象存储
（gallery PR #1491 定下的 "storage unification: only originals go to
object storage"）。实测支持这个选择 —— 小文件读取 CIFS 0.6 ms/个
优于 S3 1.4 ms/个。

**当前只收拢了缩略图 / sprite 的写侧。** 同一条 fs-vs-对象存储分支规则
在以下三处仍是内联拷贝,spec #2 把派生产物改写 S3 时需一并收编,否则
只改本类会在这三处各断一次:

- ``transcode_service.py:168-171`` —— HLS 目录落位
  （base/"derived"/"hls"/{resource_id}/{version_id} vs source.parent/"hls"）
- ``resources_crud_router.py:817-825`` —— sprite 的**读**路径,硬编码
  ``DOWNLOAD_PATH/derived/thumbnails/{resource_id}/preview_sprite.jpg``
- ``resources_crud_router.py:918-926`` —— cover 上传的**写**路径,
  ``derived/covers/{resource_id}/cover.{ext}``（其自身 docstring 承认是
  复制 thumbnail_service 的选择）
"""

from __future__ import annotations

from pathlib import Path


class DerivedArtifactPaths:
    """决定缩略图 / sprite 落在哪个目录。"""

    def thumbnail_dir(self, file_path: str, resource_id: str, local_path: Path) -> Path:
        """返回派生产物目录（已创建）。

        Args:
            file_path: 资源的存储路径,``sb://`` 或 DOWNLOAD_PATH 相对路径。
            resource_id: 资源 ID,对象存储源的目录键。
            local_path: ``materialize()`` 给出的真实本地路径。对文件系统
                源它就是源文件本身,派生产物存在它旁边;对 ``sb://`` 源它
                是个临时文件,"旁边"没有意义。

        Returns:
            已经 mkdir 好的目录路径。
        """
        from app.core.config import settings
        from app.services.library.media_storage import resolve_media_source

        loc = resolve_media_source(file_path)
        if not loc.is_object_store:
            return local_path.parent

        out_dir = (
            Path(settings.DOWNLOAD_PATH) / "derived" / "thumbnails" / str(resource_id)
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir
