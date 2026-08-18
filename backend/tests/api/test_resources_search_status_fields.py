"""GET /resources/search returns thumbnails + AI status, and nothing else.

RECON#7: ``thumbnail_url`` was hardcoded ``None``, so the @-picker had no
choice but to render a lucide placeholder for every row. The cover endpoint
(``/api/v1/resources/{id}/cover``) is already unauthenticated by design
(RECON#8), so the fix is purely "decide whether a cover exists and hand back
the URL" — mirroring the frontend ``buildThumbnailSrc`` ladder
(thumbnail_path > cover_image_path > media_id > image/* mime).

The tripwire at the bottom is the reason this file is not just two asserts:
the repo SELECT now pulls columns (file_path-adjacent storage paths, the
media FK) that must be *consumed* by the router and never *emitted*.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_active_tasks(monkeypatch):
    """Task 1b: the picker's status is now the COLUMN merged with any
    in-flight task_tracking row. Default that second half to "nothing
    running" so the tests below stay about the router, and drive it
    explicitly via ``_search(..., active=...)`` where it is the subject."""
    import app.services.ai.resource_ai_status as status_module

    async def _none(resource_ids):
        return {}

    monkeypatch.setattr(status_module, "_active_ai_tasks", _none)


# Anything in here leaking into the picker response is a finding, not a
# nitpick: storage paths are the input to the download/serve routes, and
# media_id is a Snowflake BIGINT that JS silently rounds (CLAUDE.md).
FORBIDDEN_KEYS = {
    "file_path",
    "thumbnail_path",
    "cover_image_path",
    "media_id",
    "creator_id",
    "api_key",
    "token",
    "url",
    "file_hash",
}


def _row(**over):
    base = {
        "id": "331438000000001",
        "name": "story.md",
        "mime": "text/markdown",
        "size": 100,
        "scope_type": "personal",
        "scope_id": "9",
        "updated_at": "2026-08-01T00:00:00Z",
        "thumbnail_path": None,
        "cover_image_path": None,
        "media_id": None,
        "transcript_status": "none",
        "summary_status": "none",
    }
    base.update(over)
    return base


def _search(rows, query: str = "?q=x", active: dict | None = None):
    async def _fake_list(self, **kwargs):
        return rows

    # Tab badges are their own aggregate query now. These tests are about the
    # per-row projection, so stub it — the counts contract itself is pinned in
    # test_resources_search_counts.py.
    async def _fake_counts(self, **kwargs):
        return {"all": 0, "video": 0, "image": 0, "doc": 0, "audio": 0, "pdf": 0}

    async def _fake_active(resource_ids):
        return active or {}

    def _fake_auth() -> AuthContext:
        return AuthContext(user_id="u", auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth
    try:
        with (
            patch(
                "app.repositories.resources_repository.ResourcesRepository"
                ".list_accessible_for_user",
                new=_fake_list,
            ),
            patch(
                "app.repositories.resources_repository.ResourcesRepository"
                ".count_accessible_by_kind_for_user",
                new=_fake_counts,
            ),
            patch(
                "app.services.ai.resource_ai_status._active_ai_tasks",
                new=_fake_active,
            ),
        ):
            r = client.get(f"/api/v1/resources/search{query}")
    finally:
        app.dependency_overrides.pop(get_auth, None)
    assert r.status_code == 200
    return r


COVER = "/api/v1/resources/331438000000001/cover"


# ── thumbnail_url ladder (mirrors frontend buildThumbnailSrc) ────────


def test_thumbnail_path_yields_a_cover_url():
    body = _search([_row(mime="video/mp4", thumbnail_path="thumbs/a.jpg")]).json()
    assert body["results"][0]["thumbnail_url"] == COVER


def test_cover_image_path_yields_a_cover_url():
    body = _search([_row(mime="video/mp4", cover_image_path="covers/a.jpg")]).json()
    assert body["results"][0]["thumbnail_url"] == COVER


def test_media_id_yields_a_cover_url():
    """Downloaded media keeps its cover on parsed_media, so the resource row
    carries neither path — the FK is the signal (see serve_resource_cover)."""
    body = _search([_row(mime="video/mp4", media_id=331438000000009)]).json()
    assert body["results"][0]["thumbnail_url"] == COVER


def test_image_mime_yields_a_cover_url_with_no_other_signal():
    body = _search([_row(mime="image/png")]).json()
    assert body["results"][0]["thumbnail_url"] == COVER


def test_no_signal_yields_null():
    body = _search([_row()]).json()
    assert body["results"][0]["thumbnail_url"] is None


def test_missing_columns_degrade_to_null_not_a_500():
    """Older/partial row shapes (and the pre-existing router tests) must keep
    working."""
    row = {
        "id": "1",
        "name": "story.md",
        "mime": "text/markdown",
        "size": 100,
        "scope_type": "personal",
        "scope_id": "u",
        "updated_at": "2026-05-24T10:00:00Z",
    }
    body = _search([row]).json()
    assert body["results"][0]["thumbnail_url"] is None
    assert body["results"][0]["transcript_status"] is None
    assert body["results"][0]["summary_status"] is None


# ── status passthrough ──────────────────────────────────────────────


def test_statuses_are_returned():
    body = _search(
        [
            _row(
                mime="video/mp4",
                transcript_status="processing",
                summary_status="completed",
            )
        ]
    ).json()
    assert body["results"][0]["transcript_status"] == "processing"
    assert body["results"][0]["summary_status"] == "completed"


def test_enum_members_serialize_to_their_values():
    from app.models._enums import AiTaskStatus

    body = _search(
        [
            _row(
                mime="video/mp4",
                transcript_status=AiTaskStatus.FAILED,
                summary_status=AiTaskStatus.NONE,
            )
        ]
    ).json()
    assert body["results"][0]["transcript_status"] == "failed"
    assert body["results"][0]["summary_status"] == "none"


# ── in-flight comes from task_tracking, not the column ──────────────


def test_a_running_task_shows_up_as_processing():
    """Production shape: nothing ever writes 'processing' into the column,
    so a picker chip keyed on the column alone could never light up."""
    body = _search(
        [_row(mime="video/mp4", transcript_status="none")],
        active={"331438000000001": {"transcript_status": "processing"}},
    ).json()
    assert body["results"][0]["transcript_status"] == "processing"
    assert body["results"][0]["summary_status"] == "none"


def test_a_completed_column_is_not_overwritten_by_a_task_row():
    body = _search(
        [_row(mime="video/mp4", transcript_status="completed")],
        active={"331438000000001": {"transcript_status": "processing"}},
    ).json()
    assert body["results"][0]["transcript_status"] == "completed"


# ── tripwire: the new SELECT columns must not reach the client ──────


def test_no_credential_or_path_fields_in_the_response():
    row = _row(
        mime="video/mp4",
        thumbnail_path="thumbs/secret.jpg",
        cover_image_path="covers/secret.jpg",
        media_id=331438000000009,
    )
    row["file_path"] = "downloads/u/secret-original.mp4"
    row["file_hash"] = "deadbeef"
    r = _search([row])
    body = r.json()

    result = body["results"][0]
    leaked = FORBIDDEN_KEYS & set(result)
    assert not leaked, f"picker response leaks {sorted(leaked)}"

    raw = json.dumps(body)
    assert "secret-original.mp4" not in raw
    assert "thumbs/secret.jpg" not in raw
    assert "covers/secret.jpg" not in raw
    assert "deadbeef" not in raw
    # The resource id is public (it is in the cover URL); the media FK is not.
    assert "331438000000009" not in raw
