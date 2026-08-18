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

from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth
from app.main import app

client = TestClient(app)

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


def _search(rows, query: str = "?q=x"):
    async def _fake_list(self, **kwargs):
        return rows

    def _fake_auth() -> AuthContext:
        return AuthContext(user_id="u", auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth
    try:
        with patch(
            "app.repositories.resources_repository.ResourcesRepository"
            ".list_accessible_for_user",
            new=_fake_list,
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
