"""Remove one local path without ever following a final symlink.

CLAUDE.md「形似链接的路径要用 unlink 删」: a path that may be a symlink (or a
Windows junction) is checked with ``os.path.islink`` and removed with
``os.unlink`` — unlink removes the link itself and refuses a real directory,
so it can never walk through the link into its target. ``shutil.rmtree`` is
reserved for paths already known to be real directories.

The two failure shapes this replaces:

- ``realpath`` then ``rmtree`` resolves the link FIRST, so the delete lands
  on the target's contents and the link survives.
- ``Path.is_dir()`` then ``rmtree`` follows the link to decide "directory",
  and ``rmtree`` then raises on a symlink root.
"""

from __future__ import annotations

import os
import shutil
from typing import Literal

RemovedKind = Literal["link", "directory", "file", "missing"]


def remove_local_path(
    path: str | os.PathLike[str], *, ignore_errors: bool = False
) -> RemovedKind:
    """Delete ``path`` and report what it was.

    ``"link"``: a symlink (dangling or not) — only the link was removed.
    ``"directory"``: a real directory — removed recursively; symlinks INSIDE
    it are unlinked, never followed (``shutil.rmtree`` semantics).
    ``"file"``: a regular file. ``"missing"``: nothing was there.

    ``ignore_errors`` applies to the recursive directory case only, matching
    ``shutil.rmtree``; single unlinks raise ``OSError`` for the caller.
    """
    p = os.fspath(path)
    if os.path.islink(p):
        os.unlink(p)
        return "link"
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=ignore_errors)
        return "directory"
    if os.path.lexists(p):
        os.unlink(p)
        return "file"
    return "missing"


__all__ = ["RemovedKind", "remove_local_path"]
