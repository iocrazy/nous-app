"""Sprint 7 — path traversal guard."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.boundary.errors import PathTraversalError
from app.boundary.path_guard import is_safe_path, safe_resolve


@pytest.fixture
def base(tmp_path: Path) -> Path:
    """Concrete base directory under tmp_path. Some tests plant
    symlinks / files inside it."""
    return tmp_path


@pytest.mark.unit
def test_safe_relative_resolves_under_base(base: Path):
    result = safe_resolve(base, "subdir/file.txt")
    assert result == (base / "subdir" / "file.txt").resolve()


@pytest.mark.unit
def test_dotdot_traversal_blocked(base: Path):
    with pytest.raises(PathTraversalError):
        safe_resolve(base, "../etc/passwd")


@pytest.mark.unit
def test_deep_dotdot_blocked(base: Path):
    """Multi-level traversal beyond base is rejected."""
    with pytest.raises(PathTraversalError):
        safe_resolve(base, "subdir/../../outside")


@pytest.mark.unit
def test_dotdot_inside_base_allowed(base: Path):
    """An internal .. that stays inside base is fine."""
    result = safe_resolve(base, "a/b/../c")
    assert result == (base / "a" / "c").resolve()


@pytest.mark.unit
def test_absolute_path_rejected_by_default(base: Path):
    with pytest.raises(PathTraversalError, match="absolute"):
        safe_resolve(base, "/etc/passwd")


@pytest.mark.unit
def test_absolute_path_allowed_when_inside_base(base: Path):
    """allow_absolute=True still enforces base containment."""
    inside = base / "ok.txt"
    result = safe_resolve(base, str(inside), allow_absolute=True)
    assert result == inside.resolve()


@pytest.mark.unit
def test_absolute_path_outside_base_rejected_even_when_allowed(base: Path):
    with pytest.raises(PathTraversalError, match="escapes"):
        safe_resolve(base, "/etc/passwd", allow_absolute=True)


@pytest.mark.unit
def test_symlink_escaping_base_rejected(base: Path, tmp_path_factory):
    """Planted symlink inside base pointing outside → resolved path lies
    outside, guard rejects. This is the real-world traversal vector."""
    outside = tmp_path_factory.mktemp("outside")
    secret = outside / "secret.txt"
    secret.write_text("nope")
    link = base / "shortcut"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    with pytest.raises(PathTraversalError):
        safe_resolve(base, "shortcut/secret.txt")


@pytest.mark.unit
def test_is_safe_path_returns_bool(base: Path):
    assert is_safe_path(base, "ok/file.txt") is True
    assert is_safe_path(base, "../escape") is False


@pytest.mark.unit
def test_error_message_does_not_leak_path(base: Path):
    """Error must NOT echo the rejected input — would write attacker
    paths into logs."""
    sensitive = "../../../sensitive/path/token=abc123"
    try:
        safe_resolve(base, sensitive)
        pytest.fail("should have raised")
    except PathTraversalError as exc:
        msg = str(exc)
        assert "abc123" not in msg
        assert "sensitive" not in msg
