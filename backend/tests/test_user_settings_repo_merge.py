"""Structural guarantee: UserSettingsRepository.upsert merges settings_json.

The 2026-06-02 data loss happened because a writer replaced the whole shared
``settings_json`` column. #485 fixed the one known endpoint; this moves the
merge into the single repo write path so NO caller — including a future
endpoint that forgets to read-first — can clobber keys it didn't pass.

These tests drive the real ``upsert``/``patch_settings_json`` with the DB layer
faked, asserting the payload actually written to PostgREST is the MERGED blob.
"""

import pytest

from app.repositories.user_settings_repository import (
    UserSettingsRepository,
    merge_settings_json,
)


class _FakeQuery:
    def __init__(self, sink):
        self._sink = sink

    def upsert(self, data, on_conflict=None):
        self._sink["written"] = data
        return self

    async def execute(self):
        return type("R", (), {"data": [self._sink["written"]]})()


def _repo_with(existing, sink):
    """Build a repo whose DB reads return ``existing`` and whose writes land in ``sink``."""
    repo = UserSettingsRepository()

    async def _load(_user_id):
        return existing

    async def _table():
        return _FakeQuery(sink)

    repo._load_user_settings = _load  # type: ignore[assignment]
    repo._get_table = _table  # type: ignore[assignment]
    return repo


@pytest.mark.asyncio
async def test_upsert_preserves_untouched_top_level_keys():
    """Saving a General key must NOT wipe ai_settings sitting in the same blob."""
    existing = {
        "settings_json": {
            "ai_settings": {
                "ai_providers": {"deepseek": {"enabled": True, "api_key": "sk-secret"}}
            },
            "maxConcurrentDownloads": 2,
        }
    }
    sink: dict = {}
    repo = _repo_with(existing, sink)

    # General save passes ONLY its own key (the clobber-prone shape).
    await repo.upsert("u1", {"settings_json": {"maxConcurrentDownloads": 5}})

    written = sink["written"]["settings_json"]
    assert written["maxConcurrentDownloads"] == 5  # incoming wins
    # AI provider config survives — the regression that motivated this.
    assert written["ai_settings"]["ai_providers"]["deepseek"]["api_key"] == "sk-secret"


@pytest.mark.asyncio
async def test_patch_settings_json_merges_single_subtree():
    existing = {"settings_json": {"parse_mode": "lighthttp", "ai_settings": {"x": 1}}}
    sink: dict = {}
    repo = _repo_with(existing, sink)

    await repo.patch_settings_json("u1", {"ai_settings": {"x": 2, "y": 3}})

    written = sink["written"]["settings_json"]
    assert written["parse_mode"] == "lighthttp"  # untouched key preserved
    assert written["ai_settings"] == {"x": 2, "y": 3}  # subtree replaced wholesale


@pytest.mark.asyncio
async def test_upsert_first_write_no_existing_row():
    sink: dict = {}
    repo = _repo_with(None, sink)  # no row yet

    await repo.patch_settings_json("u1", {"parse_mode": "drissionpage"})

    assert sink["written"]["settings_json"] == {"parse_mode": "drissionpage"}


@pytest.mark.asyncio
async def test_upsert_non_json_columns_skip_merge_read():
    """A download_path-only write must not trip the settings_json merge branch."""
    sink: dict = {}
    repo = _repo_with({"settings_json": {"keep": 1}}, sink)

    await repo.upsert("u1", {"download_path": "/x"})

    assert sink["written"] == {"user_id": "u1", "download_path": "/x"}
    assert "settings_json" not in sink["written"]


def test_merge_helper_still_exported_from_repo():
    assert merge_settings_json({"a": 1}, {"a": 9, "b": 2}) == {"a": 9, "b": 2}
