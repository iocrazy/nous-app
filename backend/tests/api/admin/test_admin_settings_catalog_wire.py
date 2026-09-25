"""Admin settings / alerts / table-preferences / nous-models / agent-catalog
writes: wire parity after they gained response models (OpenAPI group D1), and
the not-found paths that came with them.

Every route runs over real HTTP through the real router and the real
``get_admin_auth`` dependency (its role lookup reads a scripted session); the
repositories / services behind the handlers are replaced with fakes that
return what the real ones return. Each JSON body is compared with
``jsonable_encoder`` of the dict the handler builds — what FastAPI sent before
the model existed.

Two production defects are pinned here as well:

- every by-id alert write (PATCH / DELETE / mute / unmute / resolve) bound
  the path id as a ``str`` into a ``text()`` BIGINT comparison, which asyncpg
  rejects (``DataError``): all of them were 500s. They now bind an ``int``,
  and a non-numeric id or a miss is a typed 404.
- ``DELETE /admin/nous-models/{id}`` answered 200 "Deleted" for an id that
  matched no row.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi.encoders import jsonable_encoder
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import AiAgents
from app.repositories.agent_repository import _agent_to_dict
from app.schemas.admin_settings_catalog import AdminCatalogAgentDetail
from tests.api.wire_parity import assert_wire_unchanged, column_names, sample_orm

pytestmark = pytest.mark.unit

ADMIN = "00000000-0000-0000-0000-0000000000ad"
RULE_ID = 7_300_000_000_000_000_123  # Snowflake, above 2**53

alerts_mod = importlib.import_module("app.api.admin.alert_rules_router")
settings_mod = importlib.import_module("app.api.admin.settings_router")
prefs_mod = importlib.import_module("app.api.admin.table_preferences_router")
nous_mod = importlib.import_module("app.api.admin.nous_model_router")
agents_mod = importlib.import_module("app.api.admin.agents_router")


class _Role:
    role = "admin"


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    async def _auth() -> AuthContext:
        return AuthContext(user_id=ADMIN, auth_type="jwt")

    class _Row:
        def first(self):
            return (_Role.role,)

    class _Session:
        async def execute(self, *a, **kw):
            return _Row()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    _Role.role = "admin"
    monkeypatch.setattr("app.db.session.read_scope", _scope)
    app.dependency_overrides[get_auth] = _auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


# --------------------------------------------------------------------------- #
# POST /admin/settings/encrypt-secrets
# --------------------------------------------------------------------------- #


def _patch_selfheal(monkeypatch, summary: dict[str, Any]) -> None:
    async def _run() -> dict[str, Any]:
        return dict(summary)

    monkeypatch.setattr("app.core.secret_box.is_configured", lambda: True)
    monkeypatch.setattr(
        "app.services.infra.secrets_selfheal.run_secrets_selfheal", _run
    )


@pytest.mark.asyncio
async def test_encrypt_secrets_summary_wire_unchanged(client, monkeypatch) -> None:
    summary = {
        "ok": True,
        "errors": 1,
        "system_settings": 2,
        "platform_ai_providers": 3,
        "nous_models": 4,
        "user_mcp_servers": 5,
        "user_settings_ai_providers": 6,
    }
    _patch_selfheal(monkeypatch, summary)
    resp = await client.post("/api/v1/admin/settings/encrypt-secrets")
    assert_wire_unchanged(resp, summary)


@pytest.mark.asyncio
async def test_encrypt_secrets_skipped_run_keeps_counters_absent(
    client, monkeypatch
) -> None:
    """A skipped sweep has no counters; declared-but-unset keys stay off the
    wire instead of turning into ``null``."""
    summary = {"ok": False, "reason": "db_not_configured"}
    _patch_selfheal(monkeypatch, summary)
    resp = await client.post("/api/v1/admin/settings/encrypt-secrets")
    assert_wire_unchanged(resp, summary)


@pytest.mark.asyncio
async def test_encrypt_secrets_refused_for_a_non_admin(client, monkeypatch) -> None:
    ran: list[bool] = []

    async def _run() -> dict[str, Any]:
        ran.append(True)
        return {"ok": True}

    monkeypatch.setattr("app.core.secret_box.is_configured", lambda: True)
    monkeypatch.setattr(
        "app.services.infra.secrets_selfheal.run_secrets_selfheal", _run
    )
    _Role.role = "user"
    resp = await client.post("/api/v1/admin/settings/encrypt-secrets")
    assert resp.status_code == 403, resp.text
    assert ran == []


# --------------------------------------------------------------------------- #
# Memory promotion review: approve / reject / demote
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "fn", "key"),
    [
        ("memory/promotions/41/approve", "approve_proposal", "approved"),
        ("memory/promotions/41/reject", "reject_proposal", "rejected"),
        ("memory/41/demote", "demote_memory", "demoted"),
    ],
)
@pytest.mark.parametrize("outcome", [True, False])
async def test_memory_review_wire_unchanged(
    client, monkeypatch, path, fn, key, outcome
) -> None:
    calls: list[dict[str, Any]] = []

    async def _fake(**kw: Any) -> bool:
        calls.append(kw)
        return outcome

    monkeypatch.setattr(settings_mod, fn, _fake)
    resp = await client.post(f"/api/v1/admin/settings/{path}")
    assert_wire_unchanged(resp, {key: outcome})
    assert calls and 41 in calls[0].values()


@pytest.mark.asyncio
async def test_memory_review_non_numeric_id_is_422_not_500(client) -> None:
    resp = await client.post("/api/v1/admin/settings/memory/promotions/abc/approve")
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# /admin/alerts
# --------------------------------------------------------------------------- #


class _AlertRepo:
    def __init__(self, *, hit: bool = True) -> None:
        self.hit = hit
        self.calls: list[tuple[str, Any, Any]] = []

    async def delete_rule(self, rule_id: int) -> bool:
        self.calls.append(("delete", rule_id, None))
        return self.hit

    async def update_rule(self, rule_id: int, changes: dict[str, Any]):
        self.calls.append(("update", rule_id, changes))
        return {"id": rule_id, **changes} if self.hit else None

    async def resolve_history(self, alert_id: int) -> bool:
        self.calls.append(("resolve", alert_id, None))
        return self.hit


def _alert_repo(monkeypatch, *, hit: bool = True) -> _AlertRepo:
    repo = _AlertRepo(hit=hit)
    monkeypatch.setattr(alerts_mod, "get_alert_rules_repository", lambda: repo)
    return repo


@pytest.mark.asyncio
async def test_delete_rule_wire_unchanged_and_binds_an_int(client, monkeypatch) -> None:
    repo = _alert_repo(monkeypatch)
    resp = await client.delete(f"/api/v1/admin/alerts/rules/{RULE_ID}")
    assert_wire_unchanged(resp, {"ok": True})
    # The id must reach the BIGINT bind as an int: asyncpg rejects a str.
    assert repo.calls == [("delete", RULE_ID, None)]
    assert type(repo.calls[0][1]) is int


@pytest.mark.asyncio
async def test_mute_rule_wire_unchanged(client, monkeypatch) -> None:
    repo = _alert_repo(monkeypatch)
    resp = await client.post(
        f"/api/v1/admin/alerts/rules/{RULE_ID}/mute", params={"duration_minutes": 30}
    )
    (_, rule_id, changes) = repo.calls[0]
    assert rule_id == RULE_ID and type(rule_id) is int
    assert changes["is_muted"] is True
    # The handler builds mute_until from now(); the body must carry exactly
    # the string the rule was muted with, in the +00:00 isoformat form.
    assert_wire_unchanged(resp, {"ok": True, "mute_until": changes["mute_until"]})
    assert resp.json()["mute_until"].endswith("+00:00")
    assert datetime.fromisoformat(resp.json()["mute_until"]).tzinfo is not None


@pytest.mark.asyncio
async def test_unmute_rule_wire_unchanged(client, monkeypatch) -> None:
    repo = _alert_repo(monkeypatch)
    resp = await client.post(f"/api/v1/admin/alerts/rules/{RULE_ID}/unmute")
    assert_wire_unchanged(resp, {"ok": True})
    assert repo.calls == [("update", RULE_ID, {"is_muted": False, "mute_until": None})]


@pytest.mark.asyncio
async def test_resolve_alert_wire_unchanged(client, monkeypatch) -> None:
    repo = _alert_repo(monkeypatch)
    resp = await client.post(f"/api/v1/admin/alerts/history/{RULE_ID}/resolve")
    assert_wire_unchanged(resp, {"ok": True})
    assert repo.calls == [("resolve", RULE_ID, None)]


_ALERT_WRITES = [
    ("delete", "rules/{id}"),
    ("post", "rules/{id}/mute"),
    ("post", "rules/{id}/unmute"),
    ("post", "history/{id}/resolve"),
    ("patch", "rules/{id}"),
]


async def _call(client, method: str, path: str):
    url = f"/api/v1/admin/alerts/{path}"
    if method == "patch":
        return await client.patch(url, json={"threshold": 3})
    return await getattr(client, method)(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), _ALERT_WRITES)
async def test_alert_write_miss_is_a_typed_404(
    client, monkeypatch, method, path
) -> None:
    _alert_repo(monkeypatch, hit=False)
    resp = await _call(client, method, path.format(id=RULE_ID))
    _assert_typed_404(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), _ALERT_WRITES)
@pytest.mark.parametrize("bad", ["abc", "-1", "1.5", "99999999999999999999"])
async def test_alert_write_bad_id_is_a_typed_404_not_500(
    client, monkeypatch, method, path, bad
) -> None:
    repo = _alert_repo(monkeypatch)
    resp = await _call(client, method, path.format(id=bad))
    _assert_typed_404(resp)
    assert repo.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), _ALERT_WRITES)
async def test_alert_writes_refused_for_a_non_admin(
    client, monkeypatch, method, path
) -> None:
    repo = _alert_repo(monkeypatch)
    _Role.role = "user"
    resp = await _call(client, method, path.format(id=RULE_ID))
    assert resp.status_code == 403, resp.text
    assert repo.calls == []


@pytest.mark.asyncio
async def test_history_rule_filter_binds_an_int(client, monkeypatch) -> None:
    seen: dict[str, Any] = {}

    class _Repo:
        async def list_history(self, **kw: Any):
            seen.update(kw)
            return [], 0

    monkeypatch.setattr(alerts_mod, "get_alert_rules_repository", lambda: _Repo())
    resp = await client.get(
        "/api/v1/admin/alerts/history", params={"rule_id": str(RULE_ID)}
    )
    assert resp.status_code == 200, resp.text
    assert seen["rule_id"] == RULE_ID and type(seen["rule_id"]) is int

    resp = await client.get("/api/v1/admin/alerts/history", params={"rule_id": "x"})
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# DELETE /admin/table-preferences/{table_key}
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reset_table_preferences_wire_unchanged_and_scoped_to_caller(
    client, monkeypatch
) -> None:
    deleted: list[tuple[str, str]] = []

    class _Repo:
        async def delete(self, user_id: str, table_key: str) -> None:
            deleted.append((user_id, table_key))

    monkeypatch.setattr(
        prefs_mod, "get_admin_table_preferences_repository", lambda: _Repo()
    )
    resp = await client.delete("/api/v1/admin/table-preferences/users")
    assert_wire_unchanged(resp, {"ok": True})
    # Only the caller's own row: the key is (user_id, table_key).
    assert deleted == [(ADMIN, "users")]

    resp = await client.delete("/api/v1/admin/table-preferences/not-a-table")
    assert resp.status_code == 400, resp.text
    assert len(deleted) == 1


# --------------------------------------------------------------------------- #
# DELETE /admin/nous-models/{model_id}
# --------------------------------------------------------------------------- #


def _nous_repo(monkeypatch, *, hit: bool) -> list[str]:
    deleted: list[str] = []

    class _Repo:
        async def delete(self, model_id: str) -> bool:
            deleted.append(model_id)
            return hit

    async def _refresh() -> None:
        return None

    monkeypatch.setattr(nous_mod, "get_nous_model_repository", lambda: _Repo())
    monkeypatch.setattr(nous_mod, "refresh_catalog_windows", _refresh)
    return deleted


@pytest.mark.asyncio
async def test_delete_nous_model_wire_unchanged(client, monkeypatch) -> None:
    deleted = _nous_repo(monkeypatch, hit=True)
    resp = await client.delete(f"/api/v1/admin/nous-models/{RULE_ID}")
    assert_wire_unchanged(resp, {"message": "Deleted"})
    assert deleted == [str(RULE_ID)]


@pytest.mark.asyncio
async def test_delete_nous_model_miss_is_a_typed_404(client, monkeypatch) -> None:
    """It used to answer 200 "Deleted" for an id that matched no row."""
    _nous_repo(monkeypatch, hit=False)
    resp = await client.delete(f"/api/v1/admin/nous-models/{RULE_ID}")
    _assert_typed_404(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["abc", "99999999999999999999"])
async def test_delete_nous_model_bad_id_is_a_typed_404(
    client, monkeypatch, bad
) -> None:
    deleted = _nous_repo(monkeypatch, hit=True)
    resp = await client.delete(f"/api/v1/admin/nous-models/{bad}")
    _assert_typed_404(resp)
    assert deleted == []


# --------------------------------------------------------------------------- #
# /admin/agents (system-agent catalog)
# --------------------------------------------------------------------------- #

_LIST_KEYS = (
    "id",
    "slug",
    "name",
    "description",
    "icon",
    "model",
    "temperature",
    "max_tokens",
    "identity_md",
    "soul_md",
    "agent_md",
    "fallback_models",
    "enabled",
    "updated_at",
    "current_version",
)


def _preset_row(**overrides: Any) -> dict[str, Any]:
    """A preset exactly as ``AgentRepository`` hands it over: every
    ``ai_agents`` column, through the real ``_agent_to_dict``."""
    return _agent_to_dict(
        sample_orm(AiAgents, **{"is_system_preset": True, **overrides})
    )


class _AgentRepo:
    def __init__(self, presets: list[dict[str, Any]], *, reread: Any = "same"):
        self.presets = presets
        self.reread = reread
        self.slug_reads = 0
        self.update_error: Exception | None = None

    async def list_presets(self) -> list[dict[str, Any]]:
        return self.presets

    async def count_overrides_by_agent(self) -> dict[str, dict[str, int]]:
        return {str(self.presets[0]["id"]): {"user": 3, "team": 1}}

    async def get_by_slug(self, slug: str):
        self.slug_reads += 1
        if self.slug_reads > 1 and self.reread != "same":
            return self.reread
        return next((p for p in self.presets if p["slug"] == slug), None)

    async def update_fields_versioned(self, *a: Any, **kw: Any) -> None:
        if self.update_error is not None:
            raise self.update_error


def _agent_repo(monkeypatch, repo: _AgentRepo) -> _AgentRepo:
    monkeypatch.setattr(agents_mod, "get_agent_repository", lambda: repo)
    return repo


def test_catalog_detail_declares_every_ai_agents_column() -> None:
    """The PUT body is the whole row: a new column must be declared, or the
    model would silently drop it from the wire."""
    assert set(AdminCatalogAgentDetail.model_fields) == column_names(AiAgents) | {
        "override_counts"
    }


@pytest.mark.asyncio
async def test_list_catalog_agents_wire_unchanged(client, monkeypatch) -> None:
    full = _preset_row()
    # A hand-edited / half-seeded row: every projected column NULL.
    sparse = {"id": "00000000-0000-0000-0000-00000000000f", "slug": "sparse"}
    repo = _agent_repo(monkeypatch, _AgentRepo([full, sparse]))
    counts = await repo.count_overrides_by_agent()
    items = [
        {
            **{k: p.get(k) for k in _LIST_KEYS},
            "override_counts": counts.get(str(p["id"]), {"user": 0, "team": 0}),
        }
        for p in (full, sparse)
    ]
    resp = await client.get("/api/v1/admin/agents")
    assert_wire_unchanged(resp, {"items": items, "total": 2})
    # Numeric temperature keeps jsonable_encoder's number form.
    assert resp.json()["items"][0]["temperature"] == jsonable_encoder(
        full["temperature"]
    )


@pytest.mark.asyncio
async def test_update_catalog_agent_wire_unchanged(client, monkeypatch) -> None:
    row = _preset_row()
    _agent_repo(monkeypatch, _AgentRepo([row]))
    resp = await client.put(f"/api/v1/admin/agents/{row['slug']}", json={"name": "X"})
    assert_wire_unchanged(resp, {**row, "override_counts": {"user": 3, "team": 1}})


@pytest.mark.asyncio
async def test_update_catalog_agent_whole_number_numeric_stays_an_integer(
    client, monkeypatch
) -> None:
    """``Decimal("1")`` went out as ``1``; a ``float`` field would send ``1.0``."""
    from decimal import Decimal

    row = _preset_row(temperature=Decimal("1"), budget_per_run_cents=Decimal("50.00"))
    _agent_repo(monkeypatch, _AgentRepo([row]))
    resp = await client.put(f"/api/v1/admin/agents/{row['slug']}", json={"name": "X"})
    assert_wire_unchanged(resp, {**row, "override_counts": {"user": 3, "team": 1}})
    assert '"temperature":1,' in resp.text
    assert '"budget_per_run_cents":50.0,' in resp.text


@pytest.mark.asyncio
async def test_update_catalog_agent_vanished_before_reread_is_a_typed_404(
    client, monkeypatch
) -> None:
    """It used to answer 200 with a body holding only ``override_counts``."""
    row = _preset_row()
    _agent_repo(monkeypatch, _AgentRepo([row], reread=None))
    resp = await client.put(f"/api/v1/admin/agents/{row['slug']}", json={"name": "X"})
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_update_catalog_agent_vanished_before_write_is_a_typed_404(
    client, monkeypatch
) -> None:
    row = _preset_row()
    repo = _agent_repo(monkeypatch, _AgentRepo([row]))
    repo.update_error = ValueError("agent not found")
    resp = await client.put(f"/api/v1/admin/agents/{row['slug']}", json={"name": "X"})
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_update_catalog_agent_rejects_a_user_agent(client, monkeypatch) -> None:
    row = _preset_row(is_system_preset=False)
    _agent_repo(monkeypatch, _AgentRepo([row]))
    resp = await client.put(f"/api/v1/admin/agents/{row['slug']}", json={"name": "X"})
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kw"), [("get", {}), ("put", {"json": {"name": "X"}})]
)
async def test_catalog_refused_for_a_non_admin(client, monkeypatch, method, kw) -> None:
    row = _preset_row()
    _agent_repo(monkeypatch, _AgentRepo([row]))
    _Role.role = "user"
    path = "/api/v1/admin/agents" + ("" if method == "get" else f"/{row['slug']}")
    resp = await getattr(client, method)(path, **kw)
    assert resp.status_code == 403, resp.text
