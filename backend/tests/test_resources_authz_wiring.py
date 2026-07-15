"""Integration tests that pin the scope_guard WIRING on resources endpoints.

PR #298 closed the horizontal-authz hole on
GET /resources, GET /resources/trash, GET /resources/trash/folders, and
DELETE /resources/{resource_id} by adding
``_scope_guard: None = Depends(verify_scope_access)`` to each handler.

The existing test_scope_guards.py tests the guard FUNCTION in isolation.
These tests pin the WIRING — i.e. that a future refactor (extracting
query params to a Pydantic model, renaming a handler arg, etc.) can't
silently drop the Depends() without a test failing. PR #274 already lost
this on the read path once; this prevents the next time.

Approach: TestClient + dependency_overrides on get_auth so we can run
the actual FastAPI request pipeline (including dependency resolution)
without needing real Supabase or JWTs. The team-membership branch of
verify_scope_access is also patched (its ORM read session is stubbed) so
the tests are pure-process.

After the supabase-py → SQLAlchemy ORM transport swap the guard coerces
``int(str(scope_id))`` for the BIGINT ``team_members.team_id`` column, so
scope_ids here are numeric snowflake stand-ins (the caller's own team is
999; foreign ids are non-members).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.core.deps import AuthContext, get_auth

pytestmark = pytest.mark.integration

# user-A's personal-team snowflake stand-in (numeric — the guard binds an int).
MEMBER_TEAM_ID = 999


# ─── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    """TestClient with auth + the guard's ORM read session stubbed.

    auth always resolves to user-A, a member of team ``MEMBER_TEAM_ID``
    only. After Spec 1 PR-C unified the guard on team_members, this
    membership is what lets the personal-scope test pass while foreign
    scope_ids (888, 12345) still 403.
    """

    # Stub the read session used inside verify_scope_access so its query
    # doesn't reach a real DB. Membership is decided by inspecting the
    # statement's bound parameters for MEMBER_TEAM_ID (the guard binds the
    # requested scope_id as team_members.team_id).
    class _Result:
        def __init__(self, rows: list) -> None:
            self._rows = rows

        def first(self):
            return self._rows[0] if self._rows else None

    class _FakeSession:
        async def execute(self, stmt):
            try:
                bound = stmt.compile().params.values()
            except Exception:
                bound = ()
            is_member = MEMBER_TEAM_ID in bound
            return _Result([(MEMBER_TEAM_ID,)] if is_member else [])

    @asynccontextmanager
    async def _fake_read_scope():
        yield _FakeSession()

    import app.db.session as db_session_mod

    monkeypatch.setattr(db_session_mod, "read_scope", _fake_read_scope)

    from app.main import app

    def _auth_user_a() -> AuthContext:
        return AuthContext(user_id="user-a", auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth_user_a
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_auth, None)


# ─── Wiring assertions ────────────────────────────────────────────────
#
# We don't care here whether the handler reaches the repository or not —
# only that the guard fires BEFORE the handler runs when scope_id is not
# the caller's. A 403 from the guard is "wired"; a 200/empty (or even a
# 500 from a downstream supabase call) is "guard skipped".


def _expect_403_on_foreign_scope(client: TestClient, path: str, method: str = "GET"):
    resp = client.request(
        method,
        path,
        params={"scope_type": "personal", "scope_id": "888"},
    )
    assert resp.status_code == 403, (
        f"{method} {path} did not 403 on foreign personal scope_id "
        f"(got {resp.status_code}: {resp.text[:200]}). "
        "Guard regression: verify_scope_access not wired on this endpoint."
    )


def test_list_resources_blocks_foreign_personal_scope(client: TestClient):
    _expect_403_on_foreign_scope(client, "/api/v1/resources")


def test_list_trashed_blocks_foreign_personal_scope(client: TestClient):
    _expect_403_on_foreign_scope(client, "/api/v1/resources/trash")


def test_list_trashed_folders_blocks_foreign_personal_scope(client: TestClient):
    _expect_403_on_foreign_scope(client, "/api/v1/resources/trash/folders")


def test_delete_resource_blocks_foreign_personal_scope(client: TestClient):
    _expect_403_on_foreign_scope(
        client,
        "/api/v1/resources/some-resource-id",
        method="DELETE",
    )


def test_team_scope_without_membership_blocks(client: TestClient):
    """Sanity check the team-scope branch too — fake admin returns empty
    team_members, so verify_scope_access must 403."""
    resp = client.get(
        "/api/v1/resources",
        params={"scope_type": "team", "scope_id": "12345"},
    )
    assert resp.status_code == 403


def test_personal_scope_own_user_passes_guard(client: TestClient):
    """Caller asking for their own data must NOT be blocked by the guard.

    The handler may still 500 downstream (no real supabase) — we only
    assert it isn't 403, which would mean the guard incorrectly blocked
    the legitimate caller.
    """
    resp = client.get(
        "/api/v1/resources",
        params={"scope_type": "personal", "scope_id": str(MEMBER_TEAM_ID)},
    )
    assert resp.status_code != 403, (
        f"Guard blocked legitimate own-scope caller (got {resp.status_code}). "
        "verify_scope_access policy regression."
    )
