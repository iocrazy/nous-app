"""Admin tier scan must not lie about object-store rows.

The scan is a synchronous filesystem probe. Handed an ``sb://`` value it used
to paste that onto DOWNLOAD_PATH, land on a nonsense path, and report every
tier as missing — the panel would show a fully-transcoded video as having no
tiers. It now returns None ("unknown") for those rows instead.
"""

from __future__ import annotations

import sys

from app.api.admin.transcode_router import _scan_hls_tiers


def _mod():
    """The transcode_router MODULE.

    ``app/api/admin/__init__.py`` re-exports ``router as transcode_router``,
    which shadows the submodule of the same name — both plain ``import`` and
    ``from … import transcode_router`` hand back the APIRouter instance. Only
    sys.modules still holds the module object.
    """
    return sys.modules["app.api.admin.transcode_router"]


class TestObjectStoreRows:
    def test_returns_none_instead_of_all_false(self):
        result = _scan_hls_tiers("sb://library/hls/res1/ver1/master.m3u8")
        assert result is None, (
            "object-store rows must report unknown, not a fabricated "
            "all-tiers-missing verdict"
        )


class TestFilesystemRows:
    def test_reports_present_tiers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod().settings, "DOWNLOAD_PATH", str(tmp_path))
        hls = tmp_path / "vid" / "hls"
        (hls / "480p").mkdir(parents=True)
        (hls / "480p" / "stream.m3u8").write_text("#EXTM3U")

        result = _scan_hls_tiers("vid/hls/master.m3u8")

        assert result is not None
        assert result["480p"] is True
        assert result["720p"] is False

    def test_missing_directory_reports_all_false(self, tmp_path, monkeypatch):
        # A genuinely absent filesystem tree IS an all-false answer — that
        # verdict is trustworthy here, unlike the sb:// case.
        monkeypatch.setattr(_mod().settings, "DOWNLOAD_PATH", str(tmp_path))
        result = _scan_hls_tiers("gone/hls/master.m3u8")
        assert result is not None
        assert all(v is False for v in result.values())
