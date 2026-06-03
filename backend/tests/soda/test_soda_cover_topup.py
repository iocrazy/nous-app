"""needs_cover_topup: per-asset decision so a soda re-fetch tops up a
missing/failed cover even when the audio is already downloaded (matches the
generic per-asset model — fetch what's missing, not all-or-nothing)."""

from __future__ import annotations

from app.workflows.soda_download import needs_cover_topup

_URL_COVER = {"metadata": {"album": {"url_cover": {"uri": "x", "urls": ["https://h/"]}}}}


def test_topup_when_cover_failed_but_url_exists():
    row = {**_URL_COVER, "cover_download_status": "failed"}
    assert needs_cover_topup(row) is True


def test_topup_when_cover_never_attempted():
    row = {**_URL_COVER}
    assert needs_cover_topup(row) is True


def test_no_topup_when_cover_completed():
    row = {**_URL_COVER, "cover_download_status": "completed"}
    assert needs_cover_topup(row) is False


def test_no_topup_when_cover_file_present():
    row = {**_URL_COVER, "cover_download_path": "global/.../cover.jpg"}
    assert needs_cover_topup(row) is False


def test_no_topup_when_no_url_cover():
    # Nothing to fetch — don't loop trying a cover that doesn't exist.
    assert needs_cover_topup({"cover_download_status": "failed"}) is False
    assert needs_cover_topup({"metadata": {"album": {}}}) is False
