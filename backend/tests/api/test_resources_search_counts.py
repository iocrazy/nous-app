"""The @-picker's tab badges describe the LIBRARY, not the page on screen.

User-reported (2026-08-18, real device): opening the Video tab dropped the
Image badge to 0, the All badge never went past 50, and switching tabs made
the numbers contradict each other. Root cause: the router tallied ``counts``
by walking the rows it was about to return — rows that had already been
narrowed to one ``kind`` and already truncated at ``limit``.

These tests pin the fix at the seam that broke: the response's ``counts``
must come from the repo's dedicated aggregate, and must survive both a kinds
filter and a limit far smaller than the total.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth
from app.main import app

client = TestClient(app)

# Production shape as measured 2026-08-18 (resources, not trashed):
# video 950 / audio 293 / image 159 / doc 19. The point of using real
# magnitudes is that every one of them is > the 50-row limit ceiling.
LIBRARY_COUNTS = {
    "all": 1421,
    "video": 950,
    "audio": 293,
    "image": 159,
    "doc": 19,
    "pdf": 0,
}


@pytest.fixture(autouse=True)
def _no_active_ai_tasks(monkeypatch):
    import app.services.ai.resource_ai_status as status_module

    async def _none(resource_ids):
        return {}

    monkeypatch.setattr(status_module, "_active_ai_tasks", _none)


def _row(idx: int, mime: str = "video/mp4") -> dict:
    return {
        "id": str(331438000000000 + idx),
        "name": f"clip-{idx}.mp4",
        "mime": mime,
        "size": 10,
        "scope_type": "personal",
        "scope_id": "9",
        "updated_at": "2026-08-01T00:00:00Z",
        "thumbnail_path": None,
        "cover_image_path": None,
        "media_id": None,
        "transcript_status": "none",
        "summary_status": "none",
    }


def _get(query: str, rows: list[dict], counts: dict | None = None):
    """Call the endpoint with the repo's two reads stubbed independently.

    Keeping them separate is the whole point: the rows are one tab's clipped
    page, the counts are the library. A router that derives one from the
    other cannot pass these.
    """
    captured: dict = {}

    async def _fake_list(self, **kwargs):
        return rows

    async def _fake_counts(self, **kwargs):
        captured.update(kwargs)
        return dict(counts if counts is not None else LIBRARY_COUNTS)

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
        ):
            r = client.get(f"/api/v1/resources/search{query}")
    finally:
        app.dependency_overrides.pop(get_auth, None)
    assert r.status_code == 200
    return r.json(), captured


def test_counts_are_not_tallied_from_the_returned_page():
    """50 video rows come back; the badges still report the whole library."""
    body, _ = _get("?q=&limit=50", [_row(i) for i in range(50)])
    assert body["counts"] == LIBRARY_COUNTS
    assert len(body["results"]) == 50


def test_all_count_exceeds_the_row_limit():
    """The old tally physically could not exceed ``limit`` — that ceiling is
    what made "All 50" look plausible while the library held 1421."""
    body, _ = _get("?limit=50", [_row(i) for i in range(50)])
    assert body["counts"]["all"] == 1421
    assert body["counts"]["all"] > len(body["results"])


def test_filtering_to_one_kind_leaves_the_other_badges_intact():
    """The reported symptom, verbatim: click Video, watch Image go to 0."""
    body, _ = _get("?kinds=video&limit=50", [_row(i) for i in range(50)])
    assert body["counts"]["image"] == 159
    assert body["counts"]["audio"] == 293
    assert body["counts"]["doc"] == 19


def test_an_empty_page_still_reports_the_library():
    """No row matched this page (e.g. a kind the user has none of), which
    says nothing about how many of the OTHER kinds exist."""
    body, _ = _get("?kinds=pdf", [])
    assert body["results"] == []
    assert body["counts"]["video"] == 950


def test_counts_query_gets_q_and_scope_but_never_kinds_or_limit():
    """A badge that honoured ``kinds`` would be the original bug wearing a
    different hat; one that honoured ``limit`` would re-cap at 50."""
    _, captured = _get("?q=story&kinds=video&team_id=900123&limit=50", [])
    assert captured["q"] == "story"
    assert captured["scope_team_id"] == "900123"
    assert "kinds" not in captured
    assert "limit" not in captured


def test_counts_keys_are_exactly_the_contract():
    """The frontend indexes these six keys directly; a missing one renders
    ``undefined`` in the tab strip."""
    body, _ = _get("?q=x", [_row(0)])
    assert set(body["counts"]) == {"all", "video", "image", "doc", "audio", "pdf"}
