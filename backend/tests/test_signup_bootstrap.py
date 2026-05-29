"""Tests for ``_ensure_personal_team_bootstrap`` — the backend's
defensive copy of `handle_new_user`'s setup.

The on_auth_user_created DB trigger was dropped from prod once before
(mig 239 history). When that happens, this helper has to pick up the
slack so the welcome-bonus path further down has a team to attach to.

Tests focus on the three branches:
  - happy path: team_members already has the personal team → returns it
    without touching INSERT paths
  - cold-start path: auth.users present, no team_members → INSERTs
    user_profiles + teams (team_members lands via teams_add_owner trigger)
  - missing-user path: auth.users row not found → returns None, no writes
"""

from __future__ import annotations


import pytest

from app.api.supabase_auth_router import _ensure_personal_team_bootstrap


class _FakeTable:
    """Captures filters + chains for one .schema().table() call."""

    def __init__(self, table_name: str, owner: "_FakeClient") -> None:
        self.table_name = table_name
        self.owner = owner
        self._method_chain: list[tuple[str, tuple, dict]] = []
        self._payload = None

    def __getattr__(self, name: str):
        def _capture(*args, **kwargs) -> "_FakeTable":
            self._method_chain.append((name, args, kwargs))
            return self

        return _capture

    def insert(self, payload):
        self._method_chain.append(("insert", (payload,), {}))
        self._payload = payload
        self.owner.inserts.append((self.table_name, payload))
        return self

    def upsert(self, payload):
        self._method_chain.append(("upsert", (payload,), {}))
        self._payload = payload
        self.owner.upserts.append((self.table_name, payload))
        return self

    async def execute(self):
        return self.owner._respond(self.table_name, self._method_chain)


class _FakeSchema:
    def __init__(self, schema_name: str, owner: "_FakeClient") -> None:
        self.schema_name = schema_name
        self.owner = owner

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(f"{self.schema_name}.{name}", self.owner)


class _FakeClient:
    """Programmable Supabase client. ``responses`` keyed by qualified
    table name returns ``.data`` for execute(); ``inserts``/``upserts``
    are observed for assertion."""

    def __init__(self, responses: dict[str, list]) -> None:
        self.responses = responses
        self.inserts: list = []
        self.upserts: list = []

    def schema(self, name: str) -> _FakeSchema:
        return _FakeSchema(name, self)

    def _respond(self, table_name: str, _chain) -> object:
        class _R:
            pass

        r = _R()
        r.data = self.responses.get(table_name, [])
        return r


@pytest.fixture
def patch_admin(monkeypatch):
    def _install(client: _FakeClient) -> _FakeClient:
        async def _fake_admin():
            return client

        monkeypatch.setattr(
            "app.api.supabase_auth_router.get_async_supabase_admin", _fake_admin
        )
        return client

    return _install


@pytest.mark.asyncio
async def test_returns_existing_team_id_without_inserts(patch_admin):
    client = patch_admin(
        _FakeClient({"public.team_members": [{"team_id": 310812366953241}]})
    )

    result = await _ensure_personal_team_bootstrap(
        "8e1584e3-9c29-4a5b-90fe-125b74259f7f"
    )

    assert result == "310812366953241"
    assert client.inserts == []
    assert client.upserts == []


@pytest.mark.asyncio
async def test_cold_start_inserts_profile_and_team(patch_admin):
    """No team_members row → look up auth.users, INSERT user_profiles +
    teams. The trigger adds team_members.
    """
    client = patch_admin(
        _FakeClient(
            {
                "public.team_members": [],
                "auth.users": [
                    {
                        "id": "9f3c0eaa-...",
                        "email": "newbie@example.com",
                        "raw_user_meta_data": {"username": "newbie"},
                    }
                ],
                "public.teams": [{"id": 311999999900001}],
            }
        )
    )

    result = await _ensure_personal_team_bootstrap("9f3c0eaa-...")

    assert result == "311999999900001"
    upsert_tables = {t for t, _ in client.upserts}
    insert_tables = {t for t, _ in client.inserts}
    assert "public.user_profiles" in upsert_tables
    assert "public.teams" in insert_tables
    team_payload = next(p for t, p in client.inserts if t == "public.teams")
    assert team_payload["kind"] == "personal"
    assert team_payload["name"] == "newbie's Workspace"
    assert team_payload["owner_id"] == "9f3c0eaa-..."


@pytest.mark.asyncio
async def test_missing_auth_user_returns_none_no_writes(patch_admin):
    client = patch_admin(
        _FakeClient(
            {
                "public.team_members": [],
                "auth.users": [],
            }
        )
    )

    result = await _ensure_personal_team_bootstrap("ghost-uuid")

    assert result is None
    assert client.inserts == []
    assert client.upserts == []


@pytest.mark.asyncio
async def test_falls_back_to_email_prefix_when_username_missing(patch_admin):
    """raw_user_meta_data has no username key — use email local-part."""
    client = patch_admin(
        _FakeClient(
            {
                "public.team_members": [],
                "auth.users": [
                    {
                        "id": "abc",
                        "email": "alice@corp.com",
                        "raw_user_meta_data": {},
                    }
                ],
                "public.teams": [{"id": 999}],
            }
        )
    )

    await _ensure_personal_team_bootstrap("abc")

    team_payload = next(p for t, p in client.inserts if t == "public.teams")
    assert team_payload["name"] == "alice's Workspace"
    upsert_payload = next(p for t, p in client.upserts if t == "public.user_profiles")
    assert upsert_payload["username"] == "alice"
