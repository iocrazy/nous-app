"""GET /ai-library/agents — the ``?slug=`` filter actually filters.

Before this, ``list_agents`` had no ``slug`` parameter at all, so FastAPI
dropped the query string silently and returned the whole visible set. The
2b-2 acceptance script asked for ``?slug=script_ai``, took ``[0]`` of the
response and drove the rest of the run against ``analyze`` — a wrong agent,
with no error anywhere to say so.

This is a list filter, not a lookup: an unknown slug is an empty list, not a
404. ``GET /agents/{slug}`` already owns the 404 semantics.

Note the response shape: the endpoint's ``response_model`` is a bare
``List[AgentOut]``, so ``resp.json()`` is a list. (The ticket sketch showed
``r.json()["items"]`` — that envelope is not what this route returns, and
writing the test against the sketch rather than the wire would have made it
assert nothing.)
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock
from unittest.mock import patch as _patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import router

_USER_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    from app.core.deps import get_auth

    class _FakeAuth:
        user_id = _USER_ID
        email = "user@example.com"

    async def _grant():
        return _FakeAuth()

    app.dependency_overrides[get_auth] = _grant
    yield TestClient(app)
    app.dependency_overrides.clear()


def _agent_row(slug: str, uuid_seed: str) -> Dict[str, Any]:
    # A real UUID matters: the router skips (and logs) any row whose id won't
    # parse, so a malformed one would empty the response and make the
    # "returns []" assertions below pass for the wrong reason.
    return {
        "id": f"{uuid_seed * 8}-{uuid_seed * 4}-{uuid_seed * 4}-"
        f"{uuid_seed * 4}-{uuid_seed * 12}",
        "slug": slug,
        "name": slug.title(),
        "is_system_preset": True,
        "model": "qwen-max",
        "temperature": 0.7,
        "max_tokens": 4096,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


# Three agents, the way the acceptance script found them: `analyze` sorts
# first, so a dropped filter hands back `analyze` for a `script_ai` request.
_SEEDED = [
    _agent_row("analyze", "a"),
    _agent_row("script_ai", "b"),
    _agent_row("summarize", "c"),
]


def _seeded_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.list_accessible = AsyncMock(return_value=list(_SEEDED))
    repo.get_skill_ids = AsyncMock(return_value=[])
    repo.list_override_scopes = AsyncMock(return_value={})
    return repo


def _slugs(resp) -> List[str]:
    return [a["slug"] for a in resp.json()]


def _get(client: TestClient, url: str, repo: AsyncMock):
    with (
        _patch("app.api.ai_library_router._repos", return_value=(repo, AsyncMock())),
        _patch(
            "app.api.ai_library_router._fetch_user_team_ids",
            new=AsyncMock(return_value=[]),
        ),
        _patch(
            "app.api.ai_library_router._fetch_user_project_ids",
            new=AsyncMock(return_value=[]),
        ),
        _patch(
            "app.api.ai_library_router._enrich_agents_with_scope_names",
            new=AsyncMock(side_effect=lambda rows: rows),
        ),
    ):
        return client.get(url)


def test_slug_filter_returns_only_that_agent(client: TestClient) -> None:
    """The acceptance script hit this: asked for script_ai, got analyze."""
    repo = _seeded_repo()
    resp = _get(client, "/api/v1/ai-library/agents?slug=script_ai", repo)

    assert resp.status_code == 200
    assert _slugs(resp) == ["script_ai"]


def test_no_slug_param_returns_the_whole_visible_set(client: TestClient) -> None:
    """Absent filter must not change the pre-existing behaviour."""
    repo = _seeded_repo()
    resp = _get(client, "/api/v1/ai-library/agents", repo)

    assert resp.status_code == 200
    assert _slugs(resp) == ["analyze", "script_ai", "summarize"]


def test_unknown_slug_is_an_empty_list_not_a_404(client: TestClient) -> None:
    """It's a list filter. 404 semantics belong to GET /agents/{slug}."""
    repo = _seeded_repo()
    resp = _get(client, "/api/v1/ai-library/agents?slug=no-such-agent", repo)

    assert resp.status_code == 200
    assert resp.json() == []


def test_slug_filter_matches_exactly_not_by_prefix(client: TestClient) -> None:
    """`script` must not match `script_ai` — a prefix match would reintroduce
    the same wrong-agent failure with an extra step."""
    repo = _seeded_repo()
    resp = _get(client, "/api/v1/ai-library/agents?slug=script", repo)

    assert resp.status_code == 200
    assert resp.json() == []


def test_filtered_out_agents_are_not_enriched(client: TestClient) -> None:
    """Filter before the per-row enrichment loop: one `get_skill_ids` await,
    not one per visible agent. Guards against filtering in the response layer,
    which would be correct but do two extra round-trips per request."""
    repo = _seeded_repo()
    resp = _get(client, "/api/v1/ai-library/agents?slug=script_ai", repo)

    assert resp.status_code == 200
    assert repo.get_skill_ids.await_count == 1
