"""Path guard — defend against directory-traversal attacks.

Sprint 7 (P2 safety bundle). Every place we accept a relative path from
user / external input and join it onto a server-side directory is a
potential traversal vector ("../../etc/passwd"). The naive defense
"reject if '..' in path" is brittle — symlinks, percent-encoded ".",
absolute paths, alternative separators on Windows all bypass it.

This module gives one function: ``safe_resolve(base, untrusted)``.
It returns the absolute resolved path if-and-only-if the result lies
inside ``base``. Otherwise it raises ``PathTraversalError``.

Implementation notes:
  - Uses ``Path.resolve(strict=False)`` so the path doesn't have to exist
    (we frequently validate paths for *new* files about to be written).
  - Comparison is via ``Path.is_relative_to`` (Python 3.9+) which handles
    case-sensitivity per the host OS — appropriate since we're checking
    actual filesystem reachability.
  - Symlinks ARE resolved. If a symlink inside ``base`` points outside,
    the resolved path falls outside ``base`` and the guard rejects.
    (This is the right call — an attacker who can plant a symlink can
    smuggle arbitrary reads otherwise.)
  - Absolute paths in ``untrusted`` are rejected by default (the typical
    use case is "user gave a relative folder name"). Set
    ``allow_absolute=True`` if your caller legitimately accepts absolutes
    (rare — usually a sign you should pass a Path-already-validated-by-
    a-different-route).
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from app.boundary.errors import PathTraversalError

PathLike = Union[str, Path]


def safe_resolve(
    base: PathLike,
    untrusted: PathLike,
    *,
    allow_absolute: bool = False,
) -> Path:
    """Resolve ``untrusted`` under ``base`` with traversal protection.

    Returns the absolute resolved path. Raises ``PathTraversalError`` if
    the result would land outside ``base``, or if ``untrusted`` is
    absolute and ``allow_absolute`` is False.

    The error message is intentionally generic — it does NOT echo the
    rejected path so we don't leak it into logs / error responses.
    """
    base_path = Path(base).resolve(strict=False)
    untrusted_path = Path(untrusted)

    if untrusted_path.is_absolute() and not allow_absolute:
        raise PathTraversalError(
            "absolute paths not permitted under this base directory"
        )

    # Join then resolve. If untrusted is relative this puts us under base;
    # if it tries to escape with .. or via a planted symlink, resolve()
    # follows the chain and the final path lies outside base.
    candidate = (base_path / untrusted_path).resolve(strict=False)

    if not _is_under(candidate, base_path):
        raise PathTraversalError("resolved path escapes its allowed base directory")
    return candidate


def is_safe_path(
    base: PathLike, untrusted: PathLike, *, allow_absolute: bool = False
) -> bool:
    """Boolean variant. Useful for filtering lists without try/except."""
    try:
        safe_resolve(base, untrusted, allow_absolute=allow_absolute)
        return True
    except PathTraversalError:
        return False


def _is_under(candidate: Path, base: Path) -> bool:
    """Path.is_relative_to landed in 3.9 — this wraps it for clarity +
    explicit handling of the ValueError edge case some platforms hit
    when comparing across filesystems."""
    try:
        return candidate.is_relative_to(base)
    except ValueError:
        return False


__all__ = ["is_safe_path", "safe_resolve"]
