"""POST /projects/{id}/assets/import-from-script — 一键导入.

Two layers in one file on purpose: the ROUTE tests (fake service) pin the
gates, the scope resolution and the envelope, and the SERVICE tests (real
``AssetsService`` + the in-memory fake repos the assets suite already owns) pin
the per-name outcomes and idempotency. Splitting them would let the route's
error contract and the batch's semantics drift apart.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.services.assets import assets_service
from app.services.assets.assets_service import AssetError, AssetsService
from tests.services.assets.test_assets_service import (
    SCOPE,
    USER,
    FakeAssetsRepo,
    FakeRelationsRepo,
)

OWNER = "22222222-2222-2222-2222-222222222222"
TEAM_SCOPE = 9000
PROJECT = 55

URL = f"/api/v1/projects/{PROJECT}/assets/import-from-script"


class _AuthStub:
    user_id = USER


def item(name, asset_type, action, **over):
    """The real ``_import_one`` row, field for field — a convenient short dict
    would be accepted by the fake and rejected by ``ImportedAssetItem``, and the
    failure would be the fake's (CLAUDE.md 边界 mock 必须用真实 JSON 形状)."""
    row = {
        "name": name,
        "asset_type": asset_type,
        "action": action,
        "asset_id": None,
        "linked": False,
        "code": None,
        "detail": None,
    }
    row.update(over)
    return row


class _FakeService:
    def __init__(self):
        self.calls = []

    async def import_from_script(self, scope_id, project_id, user_id):
        self.calls.append((scope_id, project_id, user_id))
        items = [
            item("Sang Yao", "character", "created", asset_id="1", linked=True),
            item("Lin Xi", "character", "linked", asset_id="2", linked=True),
            item(
                "Rooftop",
                "location",
                "skipped",
                asset_id="3",
                linked=True,
                code="already_linked",
                detail="Asset already exists and is already referenced",
            ),
            item("", "location", "skipped", code="empty_name", detail="Blank name"),
        ]
        return {"items": items, "created": 1, "linked": 1, "skipped": 2}

    async def list_assets(self, scope_id, **f):  # the sibling GET on this prefix
        self.calls.append(("list", scope_id, f))
        return []


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(ar.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    async def _member_ok(scope_id, user_id):
        return str(scope_id) != str(TEAM_SCOPE + 1)

    async def _guard_ok(project_id, auth):
        return None

    class _Row:
        # (owner_id, team_id) — a TEAM project, so no personal-team lookup.
        def first(self):
            return (OWNER, TEAM_SCOPE)

    class _Session:
        async def execute(self, *a, **kw):
            return _Row()

    @asynccontextmanager
    async def _fake_read_scope():
        yield _Session()

    application.dependency_overrides[get_auth] = _fake_auth
    monkeypatch.setattr(ar, "_is_member", _member_ok)
    monkeypatch.setattr(ar, "verify_project_write_access", _guard_ok)
    monkeypatch.setattr(ar, "verify_project_read_access", _guard_ok)
    monkeypatch.setattr(ar, "read_scope", _fake_read_scope)
    fake = _FakeService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


# ── route: happy path ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_import_reports_every_name_and_the_tallies(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(URL)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert data["created"] == 1 and data["linked"] == 1 and data["skipped"] == 2
    # The tallies must be a view OF the items, not a second unverifiable number.
    assert data["created"] + data["linked"] + data["skipped"] == len(data["items"])
    assert [i["action"] for i in data["items"]] == [
        "created",
        "linked",
        "skipped",
        "skipped",
    ]
    assert data["items"][3]["code"] == "empty_name"
    # Scope resolved from the PROJECT, not taken from a query param.
    assert app.state.fake.calls[0] == (TEAM_SCOPE, PROJECT, USER)


@pytest.mark.asyncio
async def test_scope_comes_from_the_owners_personal_team_for_a_personal_project(
    app, monkeypatch
):
    """``projects.team_id`` NULL → the OWNER's personal team. Resolving the
    CALLER's would aim the writes at the wrong library."""

    class _Row:
        def first(self):
            return (OWNER, None)

    class _Session:
        async def execute(self, *a, **kw):
            return _Row()

    @asynccontextmanager
    async def _rs():
        yield _Session()

    async def _personal(user_id):
        assert user_id == OWNER, "resolved the caller's team, not the owner's"
        return 7777

    monkeypatch.setattr(ar, "read_scope", _rs)
    monkeypatch.setattr(ar, "_resolve_personal_team_id", _personal)

    async def _member_of_7777(scope_id, user_id):
        return str(scope_id) == "7777"

    monkeypatch.setattr(ar, "_is_member", _member_of_7777)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(URL)
    assert r.status_code == 201, r.text
    assert app.state.fake.calls[0][0] == 7777


# ── route: refusals ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_personal_project_without_owner_team_is_422(app, monkeypatch):
    class _Row:
        def first(self):
            return (OWNER, None)

    class _Session:
        async def execute(self, *a, **kw):
            return _Row()

    @asynccontextmanager
    async def _rs():
        yield _Session()

    async def _no_team(user_id):
        raise ValueError(f"No personal team found for user {user_id}")

    monkeypatch.setattr(ar, "read_scope", _rs)
    monkeypatch.setattr(ar, "_resolve_personal_team_id", _no_team)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(URL)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "personal_team_missing"
    assert app.state.fake.calls == [], "no scope resolved → nothing imported"


@pytest.mark.asyncio
async def test_non_member_of_the_resolved_scope_is_403_in_the_envelope(
    app, monkeypatch
):
    async def _never(scope_id, user_id):
        return False

    monkeypatch.setattr(ar, "_is_member", _never)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(URL)
    assert r.status_code == 403
    assert r.json() == {
        "success": False,
        "error": {
            "code": "not_a_member",
            "detail": "You are not a member of this scope",
        },
    }
    assert app.state.fake.calls == []


@pytest.mark.asyncio
async def test_project_guard_refusal_is_restated_in_this_routers_shape(
    app, monkeypatch
):
    """A project VIEWER must not import: the guard is the WRITE one, and its
    HTTPException is translated, never leaked as ``{"detail": ...}``."""
    from fastapi import HTTPException

    async def _forbidden(project_id, auth):
        raise HTTPException(status_code=403, detail="No write access")

    monkeypatch.setattr(ar, "verify_project_write_access", _forbidden)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(URL)
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "project_forbidden"
    assert "detail" not in body, "bare HTTPException shape leaked out"
    assert app.state.fake.calls == []


@pytest.mark.asyncio
async def test_read_guard_is_not_what_gates_this_write(app, monkeypatch):
    """Positive control for the test above: leaving the READ guard permissive
    while the WRITE guard refuses must still refuse. Without this, a route that
    accidentally called ``write=False`` would pass the previous test only
    because both guards were stubbed the same way."""
    from fastapi import HTTPException

    async def _forbidden(project_id, auth):
        raise HTTPException(status_code=403, detail="No write access")

    async def _ok(project_id, auth):
        return None

    monkeypatch.setattr(ar, "verify_project_write_access", _forbidden)
    monkeypatch.setattr(ar, "verify_project_read_access", _ok)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(URL)
    assert r.status_code == 403 and r.json()["error"]["code"] == "project_forbidden"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["abc", "9" * 20, "-1"])
async def test_invalid_project_id_is_422_before_any_work(app, bad):
    """``9`` * 20 parses as an int and only fails at driver BIND — a 500 handed
    out for a query string. Pinned at the boundary like every other id here."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(f"/api/v1/projects/{bad}/assets/import-from-script")
    assert r.status_code == 422, r.text
    assert app.state.fake.calls == []


# ── route: registration order ──────────────────────────────────────────────


def test_import_route_is_not_shadowed():
    """``projects_router`` is included BEFORE ``assets_router`` and owns the
    ``/projects`` prefix, so FastAPI would hand this path to any earlier route
    that matches it. It has none — but "it has none" is exactly the kind of
    fact a later ``/{project_id}/{something}`` route silently breaks."""
    from fastapi import FastAPI
    from fastapi.routing import APIRoute

    from app.api import api_router

    application = FastAPI()
    application.include_router(api_router, prefix="/api/v1")
    matches = [
        r
        for r in application.routes
        if isinstance(r, APIRoute)
        and r.path_regex.match(URL)
        and "POST" in (r.methods or set())
    ]
    assert matches, "route not registered at all"
    assert (
        matches[0].name == "import_assets_from_script"
    ), f"shadowed by {matches[0].path} ({matches[0].name})"


# ── service: per-name outcomes ─────────────────────────────────────────────


@pytest.fixture
def svc():
    # Project 55 belongs to SCOPE in the shared fake (project_teams), so
    # link_project's scope check passes for the scope these tests import into.
    return AssetsService(
        assets_repo=FakeAssetsRepo(), relations_repo=FakeRelationsRepo()
    )


def _scene_rows(characters=(), locations=()):
    """Scene rows in the shape ``ScriptSceneRepository.list_scene_rows_for_project``
    returns — ``{episode_id, location_text, content_json}``, one per scene."""
    rows = [
        {
            "episode_id": 7,
            "location_text": loc,
            "content_json": [],
        }
        for loc in locations
    ]
    rows.append(
        {
            "episode_id": 7,
            "location_text": None,
            "content_json": [{"type": "character", "text": c} for c in characters],
        }
    )
    return rows


def _stub_entities(monkeypatch, characters=(), locations=()):
    """Stub the boundary BELOW the code under test.

    The first version of these tests monkeypatched ``_script_entity_names``
    itself — the one seam between this endpoint and the rest of the system —
    so the adapter never executed. Corrupting both its dict keys and its field
    key left all 928 tests green, while production would have answered
    ``201 {"items": [], "created": 0, ...}``: a success for zero work on a
    user-triggered path.

    So: patch ``ProjectsService.get_project_entities`` instead, and build its
    return value by running the REAL ``derive_project_entities`` over real
    scene rows. The fixture cannot drift from the deriver, because the deriver
    produces it.
    """
    from app.services.library.project_entities import derive_project_entities
    from app.services.library.projects_service import ProjectsService

    payload = derive_project_entities(_scene_rows(characters, locations))

    async def _fake(self, project_id):
        return payload

    monkeypatch.setattr(ProjectsService, "get_project_entities", _fake)
    return payload


@pytest.mark.asyncio
async def test_script_entity_names_maps_the_real_wire_shape(monkeypatch):
    """The adapter, executed. ``get_project_entities`` answers
    ``{"characters": [{"name", "cue_count", "episode_ids"}], "locations":
    [{"name", "scene_count", "episode_ids"}]}`` — a literal here rather than a
    derived one, so this test also pins the shape the deriver is expected to
    emit. ``_script_entity_names`` must key off ``characters``/``locations``
    and off ``name``; any other spelling yields empty lists, which the endpoint
    would report as a cheerful 201 that imported nothing."""
    from app.services.library.projects_service import ProjectsService

    async def _fake(self, project_id):
        assert project_id == 55
        return {
            "characters": [
                {"name": "Sang Yao", "cue_count": 3, "episode_ids": ["7"]},
                {"name": "Lin Xi", "cue_count": 1, "episode_ids": []},
            ],
            "locations": [
                {"name": "Rooftop", "scene_count": 2, "episode_ids": ["7"]},
            ],
        }

    monkeypatch.setattr(ProjectsService, "get_project_entities", _fake)
    assert await assets_service._script_entity_names(55) == {
        "character": ["Sang Yao", "Lin Xi"],
        "location": ["Rooftop"],
    }


@pytest.mark.asyncio
async def test_script_entity_names_agrees_with_the_real_deriver(monkeypatch):
    """Positive control for the literal above: the same assertion, but with the
    payload produced by the production ``derive_project_entities`` rather than
    hand-written. If the deriver renames a key, the literal test alone would
    keep passing while production went empty."""
    _stub_entities(monkeypatch, characters=["Sang Yao"], locations=["Rooftop"])
    assert await assets_service._script_entity_names(55) == {
        "character": ["Sang Yao"],
        "location": ["Rooftop"],
    }


@pytest.mark.asyncio
async def test_creates_links_and_reports_each_name(svc, monkeypatch):
    _stub_entities(
        monkeypatch, characters=["Sang Yao", "Lin Xi"], locations=["Rooftop"]
    )
    out = await svc.import_from_script(SCOPE, 55, USER)
    assert out["created"] == 3 and out["linked"] == 0 and out["skipped"] == 0
    assert [i["name"] for i in out["items"]] == ["Sang Yao", "Lin Xi", "Rooftop"]
    assert [i["asset_type"] for i in out["items"]] == [
        "character",
        "character",
        "location",
    ]
    assert all(i["linked"] and i["asset_id"] for i in out["items"])
    # Every created asset carries the provenance the server asserted — not
    # "manual", which would make the library unable to tell an imported name
    # from one a human authored.
    assert {svc.assets.rows[int(i["asset_id"])]["source"] for i in out["items"]} == {
        "script_import"
    }
    assert len(svc.relations.refs) == 3


@pytest.mark.asyncio
async def test_second_run_creates_nothing(svc, monkeypatch):
    _stub_entities(monkeypatch, characters=["Sang Yao"], locations=["Rooftop"])
    first = await svc.import_from_script(SCOPE, 55, USER)
    assert first["created"] == 2
    second = await svc.import_from_script(SCOPE, 55, USER)
    assert second["created"] == 0
    assert second["skipped"] == 2 and second["linked"] == 0
    assert all(i["code"] == "already_linked" for i in second["items"])
    assert all(i["linked"] and i["asset_id"] for i in second["items"])
    # Idempotent in the DATA too, not just in the report.
    assert len(svc.assets.rows) == 2 and len(svc.relations.refs) == 2


@pytest.mark.asyncio
async def test_same_name_existing_asset_gets_the_ref_not_a_409(svc, monkeypatch):
    from app.schemas.assets import AssetCreate

    existing = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="Sang Yao"), USER
    )
    _stub_entities(monkeypatch, characters=["Sang Yao"])
    out = await svc.import_from_script(SCOPE, 55, USER)
    assert out == {
        "items": [
            {
                "name": "Sang Yao",
                "asset_type": "character",
                "action": "linked",
                "asset_id": str(existing["id"]),
                "linked": True,
                "code": None,
                "detail": None,
            }
        ],
        "created": 0,
        "linked": 1,
        "skipped": 0,
    }
    # The pre-existing row keeps its own provenance — an import does not
    # rewrite history for an asset it did not create.
    assert svc.assets.rows[int(existing["id"])]["source"] == "manual"


@pytest.mark.asyncio
async def test_a_case_differing_name_links_the_existing_asset(svc, monkeypatch):
    """``uq_assets_scope_type_name`` is on ``lower(name)`` (mig 445), so
    "Sang yao" and "Sang Yao" are the SAME asset. The import must add the ref
    to the existing row — not crash on the unique violation, and not create a
    second asset the index would reject anyway."""
    from app.schemas.assets import AssetCreate

    existing = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="Sang yao"), USER
    )
    _stub_entities(monkeypatch, characters=["Sang Yao"])
    out = await svc.import_from_script(SCOPE, 55, USER)
    assert out["created"] == 0 and out["linked"] == 1
    row = out["items"][0]
    assert row["action"] == "linked" and row["asset_id"] == str(existing["id"])
    # The script's casing does NOT rewrite the authored row's name.
    assert len(svc.assets.rows) == 1
    assert svc.assets.rows[int(existing["id"])]["name"] == "Sang yao"


@pytest.mark.asyncio
async def test_overlong_name_is_reported_not_dropped(svc, monkeypatch):
    """Through the real seam: ``derive_project_entities`` applies no length
    rule, so a 201-character cue genuinely reaches ``_import_one`` and must
    come back as a REPORTED skip — not a ValidationError that would take the
    rest of the batch with it, and not a silent drop."""
    _stub_entities(monkeypatch, characters=["X" * 201, "Good"])
    out = await svc.import_from_script(SCOPE, 55, USER)
    assert out["created"] == 1 and out["skipped"] == 1
    assert [i["code"] for i in out["items"]] == ["name_too_long", None]
    assert len(out["items"]) == 2, "a dropped name is a silent no-op"


@pytest.mark.asyncio
async def test_blank_names_are_reported_not_dropped(svc):
    """Second line of defense, tested at ``_import_one`` directly.

    A blank name cannot arrive through the seam today —
    ``derive_project_entities`` skips empty text on both branches, which the
    stub above faithfully reproduces, so feeding one through
    ``import_from_script`` would test the DERIVER's filter and call it this
    guard's. The guard still has to exist: it is what holds if the name source
    ever changes (a different deriver, an explicit name list), and its failure
    mode without it is an unhandled ValidationError mid-batch.
    """
    for raw in ("", "   "):
        row = await svc._import_one(SCOPE, 55, "character", raw, USER)
        assert row["action"] == "skipped" and row["code"] == "empty_name"
        assert row["asset_id"] is None and row["linked"] is False
    assert svc.assets.rows == {}, "a blank name must not create anything"


@pytest.mark.asyncio
async def test_one_failing_name_does_not_sink_the_batch(svc, monkeypatch):
    _stub_entities(monkeypatch, characters=["Boom", "Fine"])
    real_create = svc.create_asset

    async def _create(scope_id, payload, user_id, **kw):
        if payload.name == "Boom":
            raise AssetError(422, "asset_invalid", "nope")
        return await real_create(scope_id, payload, user_id, **kw)

    svc.create_asset = _create
    out = await svc.import_from_script(SCOPE, 55, USER)
    assert [i["action"] for i in out["items"]] == ["skipped", "created"]
    assert out["items"][0]["code"] == "asset_invalid"
    assert out["items"][0]["asset_id"] is None
    assert out["created"] == 1, "the name after the failure still landed"


@pytest.mark.asyncio
async def test_an_asset_that_lands_but_cannot_be_linked_says_so(svc, monkeypatch):
    """The two results are orthogonal: reporting only ``created`` would tell the
    caller the import succeeded for a name whose project ref never landed."""
    _stub_entities(monkeypatch, characters=["Sang Yao"])

    async def _no_link(asset_id, scope_id, project_id, user_id):
        raise AssetError(422, "project_scope_mismatch", "other team")

    svc.link_project = _no_link
    out = await svc.import_from_script(SCOPE, 55, USER)
    row = out["items"][0]
    assert row["action"] == "created" and row["asset_id"]
    assert row["linked"] is False
    assert row["code"] == "project_scope_mismatch"
    assert out["created"] == 1


@pytest.mark.asyncio
async def test_concurrent_race_without_an_existing_id_still_recovers(svc, monkeypatch):
    """``create_asset`` omits ``existing_asset_id`` when it lost a race inside a
    transaction. The import must look the winner up rather than report a skip
    for an asset that exists."""
    from app.schemas.assets import AssetCreate

    existing = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="Sang Yao"), USER
    )
    _stub_entities(monkeypatch, characters=["Sang Yao"])

    async def _raced(scope_id, payload, user_id, **kw):
        raise AssetError(409, "asset_exists", "exists")  # no extra key

    svc.create_asset = _raced
    out = await svc.import_from_script(SCOPE, 55, USER)
    assert out["items"][0]["action"] == "linked"
    assert out["items"][0]["asset_id"] == str(existing["id"])


@pytest.mark.asyncio
async def test_service_output_satisfies_the_response_model(svc, monkeypatch):
    """The route declares ``Envelope[ImportFromScriptResponse]``, which VALIDATES
    the payload on the way out. Nothing else pins the real service's dict
    against that model."""
    from app.schemas.assets import ImportFromScriptResponse

    # A mix, so the model meets a skipped row (its optional keys populated)
    # and a created one in the same payload.
    _stub_entities(
        monkeypatch, characters=["Sang Yao", "X" * 201], locations=["Rooftop"]
    )
    out = await svc.import_from_script(SCOPE, 55, USER)
    parsed = ImportFromScriptResponse.model_validate(out)
    assert len(parsed.items) == 3
    assert parsed.created == 2 and parsed.skipped == 1
    assert parsed.created + parsed.linked + parsed.skipped == len(parsed.items)


@pytest.mark.asyncio
async def test_a_failing_race_recovery_lookup_skips_one_name_not_the_batch(
    svc, monkeypatch
):
    """m-1: the one escape hatch in ``_import_one``'s "Never raises" contract.

    The recovery ``find_by_name`` runs INSIDE an ``except`` block, and Python
    does not offer a sibling handler of the same ``try`` to an exception raised
    there — so before the fix, a connection blip on that lookup escaped
    ``_import_one`` and took the entire batch down. Two names, the failing one
    first: the second must still land.
    """
    _stub_entities(monkeypatch, characters=["Raced", "Fine"])

    async def _raced(scope_id, payload, user_id, **kw):
        if payload.name == "Raced":
            raise AssetError(409, "asset_exists", "exists")  # no extra key
        return await AssetsService.create_asset(svc, scope_id, payload, user_id, **kw)

    real_find = svc.assets.find_by_name

    async def _boom(scope_id, asset_type, name):
        # Only the raced name: ``create_asset`` itself consults this repo
        # method, so a blanket failure would break the second name's CREATE
        # and the test would pass for the wrong reason.
        if name == "Raced":
            raise RuntimeError("connection reset mid-recovery")
        return await real_find(scope_id, asset_type, name)

    monkeypatch.setattr(svc, "create_asset", _raced)
    monkeypatch.setattr(svc.assets, "find_by_name", _boom)

    out = await svc.import_from_script(SCOPE, 55, USER)
    assert [i["name"] for i in out["items"]] == ["Raced", "Fine"]
    # The raced name is reported as its OWN skip, carrying the original 409's
    # code — not swallowed, and not promoted to something that looks like it
    # worked.
    assert out["items"][0]["action"] == "skipped"
    assert out["items"][0]["code"] == "asset_exists"
    assert out["items"][1]["action"] == "created", "the batch survived"
    assert out["created"] == 1 and out["skipped"] == 1


@pytest.mark.asyncio
async def test_the_defensive_detail_is_fixed_copy_not_the_exception_string(
    svc, monkeypatch
):
    """m-2: ``ImportedAssetItem.detail`` is rendered verbatim by the panel's
    ``ImportLine`` whenever the code has no translation. A SQLAlchemy exception
    string carries the statement and its bound parameters, so it must never be
    what goes on the wire. The logger already has the original."""
    from app.services.assets.assets_service import _INTERNAL_DETAIL

    secret = "SELECT * FROM assets WHERE token='sk-live-DO-NOT-SHIP'"
    _stub_entities(monkeypatch, characters=["Sang Yao"])

    async def _boom(scope_id, payload, user_id, **kw):
        raise RuntimeError(secret)

    monkeypatch.setattr(svc, "create_asset", _boom)
    row = (await svc.import_from_script(SCOPE, 55, USER))["items"][0]
    assert row["code"] == "internal_error"
    assert row["detail"] == _INTERNAL_DETAIL
    assert secret not in str(row)

    # The link half, which is a separate handler and would have been missed by
    # fixing only the one the reviewer quoted.
    async def _link_boom(asset_id, scope_id, project_id, user_id):
        raise RuntimeError(secret)

    monkeypatch.setattr(svc, "create_asset", AssetsService.create_asset.__get__(svc))
    monkeypatch.setattr(svc, "link_project", _link_boom)
    row2 = (await svc.import_from_script(SCOPE, 55, USER))["items"][0]
    assert row2["code"] == "internal_error"
    assert row2["detail"] == _INTERNAL_DETAIL
    assert secret not in str(row2)
