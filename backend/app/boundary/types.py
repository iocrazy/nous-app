"""Wrapper types that signal 'this has passed boundary validation'.

ValidatedURL inherits from str so existing httpx / yt-dlp callers accept it
unchanged at runtime. Type checkers (mypy / pyright) treat it as distinct
from str — services that declare ``url: ValidatedURL`` reject raw str.

Type-checker enforcement is best-effort (mypy not yet in CI per Sprint 1
known limits); the load-bearing runtime guard is a one-line
``assert isinstance(url, ValidatedURL)`` at every retro-fitted method's
first line. See docs/architecture/boundary-layer.md.
"""
from __future__ import annotations


class ValidatedURL(str):
    """A URL that has passed app.boundary.url_guard.validate_url.

    Inherits str so it is byte-compatible with downstream callers
    (httpx, yt-dlp subprocess args). The type identity is what matters:
    ``def fetch(url: ValidatedURL)`` is rejected by mypy when called with
    a raw string literal, and ``assert isinstance(x, ValidatedURL)`` at
    the function entry rejects raw str at runtime.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return f"ValidatedURL({super().__repr__()})"
