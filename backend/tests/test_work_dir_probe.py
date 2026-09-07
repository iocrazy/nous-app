"""The media work dir must be a real mounted volume, and writable — proven at
startup, not assumed (2026-09-07).

The transit area (yt-dlp landing, ffmpeg HLS work dir, thumbnail staging) is
``DOWNLOAD_PATH``, bind-mounted from the host. Container ``/app`` is writable
by uid 1031, so if the bind mount is missing the resolver falls back to
``/app/downloads`` and ``main.py`` creates it in the overlay layer: downloads
succeed, transcodes succeed, every probe is green — and the files sit in a
layer that is not shared with the worker and is wiped on the next deploy.
A failure that carries its own alibi. The probe below refuses that state
(readyz degraded → deploy smoke fails → rollback) instead of running on it.
"""

from __future__ import annotations

import os

import pytest

from app.startup.work_dir_probe import (
    MARKER_NAME,
    WorkDirNotMounted,
    probe_work_dir,
)


def _mounted(tmp_path):
    (tmp_path / MARKER_NAME).write_text("")
    return tmp_path


def test_a_mounted_writable_dir_passes(tmp_path):
    probe_work_dir(str(_mounted(tmp_path)), require_marker=True)


def test_missing_marker_is_refused_even_when_writable(tmp_path):
    """The overlay-layer shadow dir is writable; what it lacks is the marker
    the host's real volume carries."""
    with pytest.raises(WorkDirNotMounted, match=MARKER_NAME):
        probe_work_dir(str(tmp_path), require_marker=True)


def test_missing_dir_is_refused(tmp_path):
    with pytest.raises(WorkDirNotMounted, match="does not exist"):
        probe_work_dir(str(tmp_path / "nope"), require_marker=True)


def test_unwritable_dir_is_refused_by_a_real_write(tmp_path):
    """Read OK ≠ service OK: the CIFS incidents were all 'readable, not
    writable'. The probe writes and unlinks a file; it does not trust
    ``os.access``."""
    if os.geteuid() == 0:
        pytest.skip("root writes anywhere")
    d = _mounted(tmp_path)
    d.chmod(0o555)
    try:
        with pytest.raises(WorkDirNotMounted, match="not writable"):
            probe_work_dir(str(d), require_marker=True)
    finally:
        d.chmod(0o755)


def test_probe_leaves_nothing_behind(tmp_path):
    d = _mounted(tmp_path)
    probe_work_dir(str(d), require_marker=True)
    assert sorted(p.name for p in d.iterdir()) == [MARKER_NAME]


def test_marker_not_required_outside_the_mounted_deployment(tmp_path):
    """Dev boxes and CI have no bind mount; the marker is only demanded when
    the deployment says so (compose sets MEDIA_WORK_DIR_REQUIRE_MARKER=true).
    Writability is still proven."""
    probe_work_dir(str(tmp_path), require_marker=False)
