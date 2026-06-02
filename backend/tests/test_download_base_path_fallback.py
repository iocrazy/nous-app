"""get_download_base_path must fall back to the writable container mount when
the configured path is unusable (regression: a gateway running stale config
pointed at /home/user/downloads/douyin → PermissionError on every download it
picked up)."""

import os

from app.core.utils import Utils


def test_base_path_writable_true_for_writable_dir(tmp_path):
    assert Utils._base_path_writable(str(tmp_path)) is True
    # A not-yet-existing subdir under a writable parent is creatable → True.
    assert Utils._base_path_writable(str(tmp_path / "a" / "b" / "c")) is True


def test_base_path_writable_false_for_unwritable_root():
    # /home/user/... doesn't exist and no existing ancestor is writable.
    assert Utils._base_path_writable("/home/nonexistent_user_xyz/downloads") is False


def test_get_download_base_path_falls_back_when_configured_unwritable(
    tmp_path, monkeypatch
):
    # Simulate the gateway: configured path unwritable, but the standard
    # container mount exists + is writable → must return the fallback.
    fake_mount = tmp_path / "app_downloads"
    fake_mount.mkdir()

    import app.core.utils as u

    # No frontend_config.yml; .env points at the bad path.
    monkeypatch.setattr(u, "SERVER_CONFIG_FILE", tmp_path / "nope.yml")
    monkeypatch.setattr(u.settings, "DOWNLOAD_PATH", "/home/nonexistent_user_xyz/dl")

    real_isdir = os.path.isdir
    real_access = os.access
    monkeypatch.setattr(
        os.path, "isdir", lambda p: True if p == "/app/downloads" else real_isdir(p)
    )
    monkeypatch.setattr(
        os,
        "access",
        lambda p, m: True if p == "/app/downloads" else real_access(p, m),
    )

    assert Utils.get_download_base_path() == "/app/downloads"


def test_get_download_base_path_uses_configured_when_writable(tmp_path, monkeypatch):
    import app.core.utils as u

    monkeypatch.setattr(u, "SERVER_CONFIG_FILE", tmp_path / "nope.yml")
    monkeypatch.setattr(u.settings, "DOWNLOAD_PATH", str(tmp_path))
    assert Utils.get_download_base_path() == str(tmp_path)
