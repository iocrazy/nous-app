"""Unit tests for `_sweep_orphan_upload_dirs`.

The helper is the pure-FS half of `cleanup_orphan_storage_step`. We pass
in the DB-id set explicitly, so the test can run with a tmp tree and
no DB.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from app.workflows.scheduled_cleanup import (
    ORPHAN_STORAGE_MIN_AGE_DAYS,
    _sweep_orphan_upload_dirs,
)

ONE_DAY = 86400.0
MIN_AGE_SECONDS = ORPHAN_STORAGE_MIN_AGE_DAYS * ONE_DAY


def _make_upload_dir(
    base: Path, scope: str, rid: str, *, age_days: float, file_size: int = 1024
) -> Path:
    """Create `base/teams/{scope}/uploads/{rid}/v1/data.bin` with a
    backdated mtime."""
    rid_dir = base / "teams" / scope / "uploads" / rid / "v1"
    rid_dir.mkdir(parents=True)
    payload = rid_dir / "data.bin"
    payload.write_bytes(b"x" * file_size)
    # Backdate every dir up to and including the rid dir
    mtime = time.time() - age_days * ONE_DAY
    for path in (payload, rid_dir, rid_dir.parent):
        os.utime(path, (mtime, mtime))
    return rid_dir.parent


def test_deletes_orphan_dir_when_id_not_in_db(tmp_path: Path) -> None:
    rid_dir = _make_upload_dir(tmp_path, "team_a", "999", age_days=30)

    result = _sweep_orphan_upload_dirs(
        tmp_path, valid_resource_ids=set(), min_age_seconds=MIN_AGE_SECONDS
    )

    assert result["deleted_dirs"] == 1
    assert result["deleted_bytes"] >= 1024
    assert not rid_dir.exists()


def test_preserves_dir_when_id_present_in_db(tmp_path: Path) -> None:
    rid_dir = _make_upload_dir(tmp_path, "team_a", "999", age_days=30)

    result = _sweep_orphan_upload_dirs(
        tmp_path, valid_resource_ids={999}, min_age_seconds=MIN_AGE_SECONDS
    )

    assert result["deleted_dirs"] == 0
    assert rid_dir.exists()


def test_preserves_orphan_younger_than_min_age(tmp_path: Path) -> None:
    rid_dir = _make_upload_dir(tmp_path, "team_a", "999", age_days=1)

    result = _sweep_orphan_upload_dirs(
        tmp_path, valid_resource_ids=set(), min_age_seconds=MIN_AGE_SECONDS
    )

    assert result["deleted_dirs"] == 0
    assert result["skipped_too_young"] == 1
    assert rid_dir.exists()


def test_skips_non_numeric_dir_name(tmp_path: Path) -> None:
    weird = tmp_path / "teams" / "team_a" / "uploads" / "not_a_number"
    weird.mkdir(parents=True)
    (weird / "data.bin").write_bytes(b"x" * 16)

    result = _sweep_orphan_upload_dirs(
        tmp_path, valid_resource_ids=set(), min_age_seconds=MIN_AGE_SECONDS
    )

    assert result["deleted_dirs"] == 0
    assert result["skipped_unparseable"] == 1
    assert weird.exists()


def test_handles_missing_teams_root(tmp_path: Path) -> None:
    # No `teams/` subdir at all — must not raise
    result = _sweep_orphan_upload_dirs(
        tmp_path, valid_resource_ids={1, 2, 3}, min_age_seconds=MIN_AGE_SECONDS
    )

    assert result == {
        "deleted_dirs": 0,
        "deleted_bytes": 0,
        "skipped_too_young": 0,
        "skipped_unparseable": 0,
    }


def test_mixed_tree(tmp_path: Path) -> None:
    """One kept (in DB), one deleted (orphan + old), one preserved (orphan
    but too young), one weird-name."""
    keep = _make_upload_dir(tmp_path, "team_a", "100", age_days=30)
    deletable = _make_upload_dir(tmp_path, "team_a", "200", age_days=30)
    too_young = _make_upload_dir(tmp_path, "team_b", "300", age_days=1)
    weird = tmp_path / "teams" / "team_b" / "uploads" / "junk"
    weird.mkdir(parents=True)
    (weird / "x").write_text("x")

    result = _sweep_orphan_upload_dirs(
        tmp_path, valid_resource_ids={100}, min_age_seconds=MIN_AGE_SECONDS
    )

    assert result["deleted_dirs"] == 1
    assert result["skipped_too_young"] == 1
    assert result["skipped_unparseable"] == 1
    assert keep.exists()
    assert not deletable.exists()
    assert too_young.exists()
    assert weird.exists()
