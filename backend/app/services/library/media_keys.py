"""对象键构造。从 media_storage.py 原样搬出,逻辑未改。

media_storage 保留同名模块级函数作为薄转发,故本次改动对 12 个既有
调用方零影响。
"""

from __future__ import annotations

import mimetypes
import re
from typing import Optional

_SB_SCHEME = "sb://"

# ── HLS keys: path-addressed, NOT content-addressed ─────────────────────────
#
# Every other sb:// writer goes through content_key(): key derived from the
# sha256 so identical bytes dedup and the original filename never lands in the
# key. HLS cannot use that scheme — an m3u8 references its siblings by bare
# relative path (``480p/stream.m3u8``, ``segment_000.ts``), so renaming a
# segment to its hash breaks every playlist that points at it.
#
# So HLS gets a second, path-addressed namespace rooted at ``hls/``. That
# prefix cannot collide with the content-addressed one, which always starts
# ``t{scope_id}/``. The trade-off is losing cross-resource dedup, which costs
# nothing here: segments of different videos are never byte-identical.
_HLS_PREFIX = "hls"
# resource_id / version_id reach the key from the DB, so pin their shape rather
# than trusting the caller — this namespace is the one place a caller-supplied
# value would otherwise flow into a key path.
_HLS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Relative parts come from walking the ffmpeg output dir; keep them to plain
# segment/playlist names so a crafted path can never climb out of the prefix.
_HLS_REL_RE = re.compile(
    r"^[A-Za-z0-9_][A-Za-z0-9._-]*(/[A-Za-z0-9_][A-Za-z0-9._-]*)*$"
)


class MediaKeyBuilder:
    """构造对象存储键与 sb:// 路径。无状态,可自由实例化。"""

    def ext_for(self, mime: str, filename: Optional[str]) -> str:
        """Pick a file extension: prefer the original filename's, else sniff mime."""
        if filename and "." in filename:
            ext = filename.rsplit(".", 1)[-1].lower()
            # keep it sane (letters/digits, ≤5 chars) so a weird name can't inject
            if ext.isalnum() and len(ext) <= 5:
                return f".{ext}"
        guessed = mimetypes.guess_extension((mime or "").split(";")[0].strip() or "")
        return guessed or ".bin"

    def _object_key(self, scope_id: int, sha: str, ext: str) -> str:
        return f"t{scope_id}/{sha[:2]}/{sha[2:4]}/{sha}{ext}"

    def content_key_from_sha(
        self, scope_id: int, sha: str, mime: str, filename: Optional[str] = None
    ) -> str:
        """构造对象键 —— 从已算好的 sha 直接构键（调用方自行负责 sha 的
        计算方式：整块 hash 或流式 hash 均可，本方法不关心来源）。"""
        return self._object_key(scope_id, sha, self.ext_for(mime, filename))

    def hls_prefix(self, resource_id: str, version_id: str) -> str:
        """Key prefix owning one version's HLS output: ``hls/{rid}/{vid}``."""
        rid, vid = str(resource_id), str(version_id)
        if not _HLS_ID_RE.match(rid) or not _HLS_ID_RE.match(vid):
            raise ValueError(f"unsafe HLS id: resource={rid!r} version={vid!r}")
        return f"{_HLS_PREFIX}/{rid}/{vid}"

    def hls_key(self, resource_id: str, version_id: str, rel_path: str) -> str:
        """Full key for one HLS artefact, e.g. ``hls/{rid}/{vid}/480p/stream.m3u8``.

        ``rel_path`` is the artefact's path relative to the playlist root — exactly
        what the m3u8 references — so relative links keep resolving once the tree
        lives in the object store.
        """
        rel = str(rel_path).strip("/")
        if not rel or ".." in rel.split("/") or not _HLS_REL_RE.match(rel):
            raise ValueError(f"unsafe HLS relative path: {rel_path!r}")
        return f"{self.hls_prefix(resource_id, version_id)}/{rel}"

    def to_file_path(self, bucket: str, key: str) -> str:
        """Compose the ``sb://`` value stored in generated_media.file_path."""
        return f"{_SB_SCHEME}{bucket}/{key}"

    def album_prefix(self, scope_id: int, resource_id) -> str:
        """Prefix owning an album's flattened objects: ``t{scope}/album/{rid}/``.

        Deliberately shares the content-addressed ``t{scope_id}/`` root (not a
        dedicated namespace like HLS's ``hls/``) — a two-hex-char sha shard can
        never literally read ``al`` (``l`` is not a hex digit), so this cannot
        collide with a content-addressed key under the same scope. The
        trailing slash is load-bearing: it is what makes
        ``MediaLocation.is_prefix`` true, telling the reader "list everything
        under this key" instead of "GET this one object".
        """
        return f"t{scope_id}/album/{resource_id}/"

    def derived_prefix(self, resource_id) -> str:
        """Prefix owning one resource's derived assets: ``derived/{rid}/``.

        Thumbnails / preview sprites / covers are keyed purely by
        resource_id — deliberately NOT content-addressed like
        ``content_key_from_sha`` (there is no scope_id concept for a derived
        asset, and the reader must be able to locate it from resource_id
        alone, with no DB lookup — see resources_crud_router.py's
        ``serve_preview_sprite``, which has no DB column to consult for
        preview_sprite.jpg at all). Own top-level namespace (not nested under
        ``t{scope}/`` like albums) since derived assets don't belong to any
        one scope's content pool.
        """
        return f"derived/{resource_id}/"
