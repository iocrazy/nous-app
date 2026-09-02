"""Assets endpoints: payload shape + error mapping + scope gating (no DB)."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.services.assets.assets_service import AssetError

USER = "11111111-1111-1111-1111-111111111111"
NOW = "2026-08-29T00:00:00+00:00"


class _AuthStub:
    user_id = USER


# Every /assets route now declares ``response_model=Envelope[...]``, so FastAPI
# validates what the service handed back. A fake that answers with a convenient
# three-key dict would fail that validation — and the failure would be the
# fake's, not the router's. These builders mirror the real serialized row
# (``_serialize(with_derived(...))`` / ``_serialize_file`` / ``_serialize_link``
# / ``_serialize_loadout``) field for field: CLAUDE.md "边界 mock 必须用真实
# JSON 形状".


def asset_row(**over):
    row = {
        "id": "1",
        "scope_id": "9000",
        "asset_type": "character",
        "subtype": None,
        "name": "Sang Yao",
        "role_tag": "",
        "description": "",
        "attrs": {},
        "prompt_positive": None,
        "prompt_negative": None,
        "prompt_positive_zh": None,
        "prompt_negative_zh": None,
        "platform_params": {},
        "cover_file_id": None,
        "source": "manual",
        "duplicated_from": None,
        "is_system_preset": False,
        # mig 449 — ``_serialize`` emits every ``assets`` column, so the fixture
        # carries it too. Leaving it out would let the response model's default
        # stand in for a field the real wire always sends.
        "in_library": True,
        "tags": {},
        "sort_order": 0,
        "created_by": USER,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
        "readiness": {"state": "draft", "missing": ["sheet"]},
        "file_counts_by_slot": {},
        "project_ids": [],
        "loadout_count": 1,
    }
    row.update(over)
    return row


def detail_row(**over):
    # ``used_in`` rides along because ``response_model`` DROPS undeclared keys
    # and DEFAULTS missing ones — a fixture without it would answer 200 with an
    # empty ``used_in`` whether or not the route still emits it.
    row = asset_row(
        files=[],
        links=[],
        linked_by=[],
        loadouts=[],
        used_in={"canvases": [], "storyboards": []},
    )
    row.update(over)
    return row


def file_row(**over):
    row = {
        "asset_id": "5",
        "resource_id": "727145299382534146",
        "slot": "sheet",
        "loadout_id": None,
        "sort_order": 0,
        "note": None,
        "attached_by": USER,
        "attached_at": NOW,
    }
    row.update(over)
    return row


def loadout_row(**over):
    row = {
        "id": "6",
        "asset_id": "5",
        "name": "Night raid",
        "is_default": False,
        "costume_ids": [],
        "prop_ids": [],
        "prompt_extra": None,
        "sort_order": 0,
        "created_at": NOW,
    }
    row.update(over)
    return row


class _FakeService:
    def __init__(self):
        self.calls = []
        self.attach_attempts = []
        self.created_sources = []

    async def list_assets(self, scope_id, **f):
        self.calls.append(("list", scope_id, f))
        return [asset_row()]

    # Overridable per test; the default is a full six-key tally so callers that
    # do not care about counts still get a body the response model accepts.
    counts = None

    async def count_by_type(self, scope_id):
        self.calls.append(("count_by_type", scope_id))
        if self.counts is not None:
            return self.counts
        return {
            "character": 2,
            "location": 1,
            "prop": 0,
            "costume": 0,
            "prompt": 3,
            "audio": 0,
        }

    # Provenance the router actually handed down, so the "defaults to manual"
    # pin reads the value rather than the absence of a rejection. Instance
    # attribute only (see __init__) — a class-level list would be shared by
    # every test in the file.
    async def create_asset(self, scope_id, payload, user_id):
        self.created_sources.append(payload.source)
        if payload.name == "dup":
            raise AssetError(409, "asset_exists", "exists", {"existing_asset_id": "7"})
        return asset_row(id="2", name=payload.name, asset_type=payload.asset_type)

    async def get_asset(self, asset_id, scope_id):
        if asset_id == 404:
            raise AssetError(404, "asset_not_found", "nope")
        return detail_row(id=str(asset_id))

    # The bundle's selection is recorded RAW so a test can tell the three wire
    # states apart: absent (None), given, and given-but-empty. Collapsing any
    # two of those here would hide exactly the bug the router's normalization
    # exists to prevent.
    async def get_bundle(self, asset_id, scope_id, **kw):
        self.calls.append(("bundle", scope_id, dict(kw)))
        return {
            "prompt": {"positive": "p", "negative": ""},
            "reference_resource_ids": [],
            "dropped": [],
            "max_refs": 3,
        }

    # Set to a resource_id that should blow up, to exercise the batch path.
    fail_on_resource_id = None

    async def attach_file(self, asset_id, scope_id, req, user_id):
        self.attach_attempts.append(req.resource_id)
        if req.resource_id == self.fail_on_resource_id:
            raise AssetError(404, "resource_not_found", "nope")
        return file_row(
            asset_id=str(asset_id), resource_id=req.resource_id, slot=req.slot
        )

    async def add_link(self, asset_id, scope_id, req):
        raise AssetError(422, "link_not_allowed", "no", {})

    async def duplicate(self, asset_id, scope_id, user_id, req):
        self.calls.append(
            ("duplicate", scope_id, {"asset_id": asset_id, "name": req.name})
        )
        if asset_id == 409:
            raise AssetError(409, "asset_exists", "exists", {"existing_asset_id": "7"})
        return detail_row(
            id="99",
            name=req.name or "Sang Yao (copy)",
            source="duplicated",
            duplicated_from=str(asset_id),
        )


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(ar.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    async def _member_ok(scope_id, user_id):
        return scope_id != "666"

    @asynccontextmanager
    async def _no_uow():
        yield None

    application.dependency_overrides[get_auth] = _fake_auth
    monkeypatch.setattr(ar, "_is_member", _member_ok)
    # The fake service never touches a DB, so the real unit_of_work() would try
    # to open a session. Atomicity itself is the transaction's job (asserted in
    # the fix report against its docstring); what the fake CAN prove is that the
    # loop stops at the first failure.
    monkeypatch.setattr(ar, "unit_of_work", _no_uow)
    fake = _FakeService()
    monkeypatch.setattr(ar, "_service", lambda: fake)
    application.state.fake = fake
    return application


@pytest.mark.asyncio
async def test_list_passes_filters_and_wraps(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            "/api/v1/assets?scope_id=9000&type=character&project_id=55&q=sang"
        )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["data"][0]["name"] == "Sang Yao"
    _, scope, f = app.state.fake.calls[0]
    assert (
        scope == 9000
        and f["asset_type"] == "character"
        and f["project_id"] == 55
        and f["q"] == "sang"
    )


@pytest.mark.asyncio
async def test_non_member_403(app):
    """I4: the gate answers in the SAME envelope as every other failure. It used
    to raise a bare HTTPException, so the one 403 that matters most was the only
    failure with no ``code`` for a client to branch on."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets?scope_id=666")
    assert r.status_code == 403
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "not_a_member"
    assert "detail" not in body, "bare HTTPException shape leaked out"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/api/v1/assets?scope_id=666"),
        ("get", "/api/v1/assets/counts?scope_id=666"),
        ("get", "/api/v1/assets/5?scope_id=666"),
        ("get", "/api/v1/assets/5/bundle?scope_id=666&model=m"),
        ("delete", "/api/v1/assets/5?scope_id=666"),
        ("post", "/api/v1/assets/5/duplicate?scope_id=666"),
        ("post", "/api/v1/assets/5/files?scope_id=666"),
        ("delete", "/api/v1/assets/5/files/6/sheet?scope_id=666"),
        ("post", "/api/v1/assets/5/links?scope_id=666"),
        ("delete", "/api/v1/assets/5/links/6/wears?scope_id=666"),
        ("post", "/api/v1/assets/5/loadouts?scope_id=666"),
        ("patch", "/api/v1/assets/5/loadouts/6?scope_id=666"),
        ("delete", "/api/v1/assets/5/loadouts/6?scope_id=666"),
        ("post", "/api/v1/assets/5/project-refs?scope_id=666"),
        ("delete", "/api/v1/assets/5/project-refs/7?scope_id=666"),
        ("post", "/api/v1/assets/5/prompt/translate?scope_id=666"),
        ("post", "/api/v1/assets/5/prompt/regenerate?scope_id=666"),
    ],
)
async def test_every_gated_route_uses_the_error_envelope(app, method, url):
    bodies = {
        "post": {
            "files": {"resource_id": "1", "slot": "sheet"},
            "links": {"to_asset_id": "6", "relation": "wears"},
            "loadouts": {"name": "Night"},
            "project-refs": {"project_id": "7"},
            # Body validation runs BEFORE the handler, so a route with a
            # required body needs a VALID one here or the gate never gets to
            # answer and this sweep would pin a 422 instead of the 403.
            "prompt/translate": {"target_lang": "zh"},
        }
    }
    json_body = None
    if method in ("post", "patch"):
        json_body = next(
            (v for k, v in bodies["post"].items() if f"/{k}" in url), {"name": "Night"}
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await getattr(c, method)(url, **({"json": json_body} if json_body else {}))
    assert r.status_code == 403, r.text
    assert r.json() == {
        "success": False,
        "error": {
            "code": "not_a_member",
            "detail": "You are not a member of this scope",
        },
    }


@pytest.mark.asyncio
async def test_create_201_and_409_shape(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets?scope_id=9000", json={"asset_type": "prop", "name": "Blade"}
        )
        assert r.status_code == 201 and r.json()["data"]["id"] == "2"
        r = await c.post(
            "/api/v1/assets?scope_id=9000", json={"asset_type": "prop", "name": "dup"}
        )
    assert r.status_code == 409
    err = r.json()
    assert err["success"] is False
    assert (
        err["error"]["code"] == "asset_exists"
        and err["error"]["existing_asset_id"] == "7"
    )


@pytest.mark.asyncio
async def test_get_404_mapping(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/assets/404?scope_id=9000")
    assert r.status_code == 404 and r.json()["error"]["code"] == "asset_not_found"


@pytest.mark.asyncio
async def test_attach_single_and_batch(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={"resource_id": "727145299382534146", "slot": "sheet"},
        )
        assert r.status_code == 201 and r.json()["data"]["slot"] == "sheet"
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={
                "items": [{"resource_id": "1"}, {"resource_id": "2", "slot": "stills"}]
            },
        )
    assert r.status_code == 201 and len(r.json()["data"]) == 2


@pytest.mark.asyncio
async def test_link_422_mapping(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/links?scope_id=9000",
            json={"to_asset_id": "6", "relation": "wears"},
        )
    assert r.status_code == 422 and r.json()["error"]["code"] == "link_not_allowed"


# ── I-1: ids that reach int() are pinned at the boundary ───────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/assets?scope_id=abc",
        "/api/v1/assets?scope_id=",
        "/api/v1/assets?scope_id=9000&project_id=abc",
        "/api/v1/assets/5?scope_id=abc",
        "/api/v1/projects/abc/assets",
    ],
)
async def test_non_numeric_ids_are_422_not_500(app, url):
    """A bare str param would reach int() and raise ValueError → 500."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(url)
    assert r.status_code == 422, r.text


# ── I-2: batch attach is all-or-nothing ────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_attach_stops_at_first_failure_and_reports_it(app):
    app.state.fake.fail_on_resource_id = "2"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={
                "items": [
                    {"resource_id": "1"},
                    {"resource_id": "2"},
                    {"resource_id": "3"},
                ]
            },
        )
    assert r.status_code == 404
    body = r.json()
    assert body["success"] is False and body["error"]["code"] == "resource_not_found"
    # Nothing after the failing item was attempted — no work past the raise.
    assert app.state.fake.attach_attempts == ["1", "2"]


@pytest.mark.asyncio
async def test_batch_attach_runs_inside_one_unit_of_work(app, monkeypatch):
    """The rollback of items 1..N-1 is the transaction's job, so the thing to
    pin here is that the loop really is wrapped in one."""
    entered = []

    @asynccontextmanager
    async def _spy():
        entered.append("enter")
        yield None

    monkeypatch.setattr(ar, "unit_of_work", _spy)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={"items": [{"resource_id": "1"}, {"resource_id": "2"}]},
        )
    assert r.status_code == 201
    assert entered == ["enter"], "batch must run in exactly one unit_of_work()"


@pytest.mark.asyncio
async def test_single_attach_opens_no_unit_of_work(app, monkeypatch):
    """One write commits on its own; wrapping it would be pointless overhead."""
    entered = []

    @asynccontextmanager
    async def _spy():
        entered.append("enter")
        yield None

    monkeypatch.setattr(ar, "unit_of_work", _spy)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/files?scope_id=9000",
            json={"resource_id": "1", "slot": "sheet"},
        )
    assert r.status_code == 201 and entered == []


# ── I-3: personal-project fallback uses the OWNER's team ───────────────────


@pytest.mark.asyncio
async def test_personal_project_resolves_owner_team_not_callers(app, monkeypatch):
    """A collaborator (project_members) on someone else's personal project must
    see the owner's scope; resolving the caller's own team would return an
    empty list that is indistinguishable from an empty library."""
    OWNER = "22222222-2222-2222-2222-222222222222"

    async def _guard_ok(project_id, auth):
        return None

    class _Row:
        def first(self):
            return (OWNER, None)  # team_id NULL → personal project

    class _Session:
        async def execute(self, *a, **kw):
            return _Row()

    @asynccontextmanager
    async def _fake_read_scope():
        yield _Session()

    seen = {}

    async def _fake_resolver(user_id):
        seen["user_id"] = user_id
        return "777"

    monkeypatch.setattr(ar, "verify_project_read_access", _guard_ok)
    monkeypatch.setattr(ar, "read_scope", _fake_read_scope)
    monkeypatch.setattr(ar, "_resolve_personal_team_id", _fake_resolver)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/projects/55/assets")

    assert r.status_code == 200
    assert seen["user_id"] == OWNER, "resolved the caller's team, not the owner's"
    _, scope, f = app.state.fake.calls[0]
    assert scope == 777 and f["project_id"] == 55


# ── I2: project-refs are WRITES, and both halves are guarded the same ──────


@pytest.fixture
def guard_spy(monkeypatch):
    """Records which project guard each route reached. ``_project_gate`` resolves
    the guard by module global at call time, so patching the names here is what
    the router really calls."""
    seen = {"read": [], "write": []}

    async def _read(project_id, auth):
        seen["read"].append(str(project_id))

    async def _write(project_id, auth):
        seen["write"].append(str(project_id))

    monkeypatch.setattr(ar, "verify_project_read_access", _read)
    monkeypatch.setattr(ar, "verify_project_write_access", _write)
    return seen


@pytest.mark.asyncio
async def test_link_project_ref_uses_the_write_guard(app, guard_spy):
    """``_resolve_project_access`` grants can_read to ANY project_members row —
    a viewer. Creating an asset_project_refs row is a write."""

    async def _link(asset_id, scope_id, project_id, user_id):
        return None

    app.state.fake.link_project = _link
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets/5/project-refs?scope_id=9000", json={"project_id": "77"}
        )
    assert r.status_code == 201 and r.json()["data"] == {"linked": True}
    assert guard_spy["write"] == ["77"]
    assert guard_spy["read"] == [], "a write path must not gate on read access"


@pytest.mark.asyncio
async def test_unlink_project_ref_uses_the_write_guard(app, guard_spy):
    """This route had NO project guard at all — the two halves of one operation
    were guarded differently."""

    async def _unlink(asset_id, scope_id, project_id):
        return None

    app.state.fake.unlink_project = _unlink
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.delete("/api/v1/assets/5/project-refs/77?scope_id=9000")
    assert r.status_code == 200 and r.json()["data"] == {"unlinked": True}
    assert guard_spy["write"] == ["77"]
    assert guard_spy["read"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,expected_code",
    [(403, "project_forbidden"), (404, "project_not_found")],
)
async def test_project_guard_refusal_wears_the_asset_envelope(
    app, monkeypatch, status_code, expected_code
):
    """The guards raise FastAPI HTTPExceptions ({"detail": ...}); on this router
    they must come back in the one shape clients branch on."""
    from fastapi import HTTPException

    async def _deny(project_id, auth):
        raise HTTPException(status_code=status_code, detail="nope")

    monkeypatch.setattr(ar, "verify_project_write_access", _deny)
    monkeypatch.setattr(ar, "verify_project_read_access", _deny)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        post = await c.post(
            "/api/v1/assets/5/project-refs?scope_id=9000", json={"project_id": "77"}
        )
        delete = await c.delete("/api/v1/assets/5/project-refs/77?scope_id=9000")
        listing = await c.get("/api/v1/projects/77/assets")
    for r in (post, delete, listing):
        assert r.status_code == status_code, r.text
        body = r.json()
        assert body["success"] is False and body["error"]["code"] == expected_code


# ── M1: a query-string id past int64 is 422, not a 500 at driver BIND ──────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/assets?scope_id=99999999999999999999",
        "/api/v1/assets?scope_id=9000&project_id=99999999999999999999",
        "/api/v1/assets/99999999999999999999?scope_id=9000",
        "/api/v1/projects/99999999999999999999/assets",
    ],
)
async def test_ids_past_int64_are_422_not_500(app, url):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(url)
    assert r.status_code == 422, r.text


# ── M4: only the server may claim the non-manual provenances ───────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["migrated", "duplicated", "system_preset", "script_import"]
)
async def test_create_refuses_a_client_claimed_server_source(app, source):
    """These four are assertions only the server can honestly make, each
    written alongside the row that makes it true (``duplicated_from`` for
    ``duplicated``, the preset flag + NULL scope for ``system_preset``). A
    client that could set them would write a permanently wrong provenance —
    it is stamped once at creation and never corrected — that nothing
    downstream could tell from the real thing.

    The refusal must be a 422 at the schema boundary, not a service check: a
    value that reaches the service has already been model_dump()-ed into the
    INSERT's field dict.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets?scope_id=9000",
            json={"asset_type": "character", "name": "Forged", "source": source},
        )
    assert r.status_code == 422, r.text
    assert any(
        "source" in map(str, err.get("loc", [])) for err in r.json()["detail"]
    ), r.text
    # And it never reached the service — a 422 raised AFTER the create would
    # leave the row behind. Read from ``created_sources``, which
    # ``_FakeService.create_asset`` actually appends to: ``calls`` is only
    # written by list/count/attach, so asserting on it here would be true even
    # when the router did call through.
    assert app.state.fake.created_sources == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["manual", "generated"])
async def test_create_still_accepts_the_two_client_sources(app, source):
    """The other half of the pin. ``generated`` is what ``SaveAsAssetDialog``
    sends for every asset born out of the Generated inbox; narrowing the
    allowlist onto it would break that flow silently (the dialog reports the
    error, the asset just never exists)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets?scope_id=9000",
            json={"asset_type": "character", "name": "Sang Yao", "source": source},
        )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_create_defaults_to_manual_when_source_is_omitted(app):
    """Omission is not "unknown" — the server default is a claim, and it has to
    stay the honest one."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/assets?scope_id=9000",
            json={"asset_type": "character", "name": "Sang Yao"},
        )
    assert r.status_code == 201, r.text
    assert app.state.fake.created_sources == ["manual"]


# ── the bundle's selection parameter, on the wire (C1) ─────────────────────
#
# THREE states, and a query string can only tell them apart because the empty
# one is spelled `?selected_file_ids=` rather than by omitting the parameter.
# These three tests are the pin on that spelling: without them, "absent" and
# "empty" collapse into each other and unticking every box on a card would ship
# every reference the user just removed.


async def _bundle_kwargs(app, query):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(f"/api/v1/assets/5/bundle?scope_id=9000&model=m{query}")
    assert r.status_code == 200, r.text
    kind, _, kw = app.state.fake.calls[-1]
    assert kind == "bundle"
    return kw


@pytest.mark.asyncio
async def test_an_absent_selection_reaches_the_service_as_none(app):
    """The asset sheet's request, unchanged by this parameter's arrival."""
    kw = await _bundle_kwargs(app, "")
    assert kw["selected_file_ids"] is None


@pytest.mark.asyncio
async def test_a_repeated_selection_arrives_in_order(app):
    kw = await _bundle_kwargs(
        app, "&selected_file_ids=727145299382534146&selected_file_ids=8"
    )
    assert kw["selected_file_ids"] == ("727145299382534146", "8")


@pytest.mark.asyncio
async def test_an_empty_selection_is_empty_not_absent(app):
    """`?selected_file_ids=` is "the user unticked everything". It must NOT
    arrive as None — that is the state that means "send them all"."""
    kw = await _bundle_kwargs(app, "&selected_file_ids=")
    assert kw["selected_file_ids"] == ()
    assert kw["selected_file_ids"] is not None


@pytest.mark.asyncio
async def test_a_blank_entry_among_real_ids_is_dropped_not_carried(app):
    kw = await _bundle_kwargs(app, "&selected_file_ids=7&selected_file_ids=%20")
    assert kw["selected_file_ids"] == ("7",)


@pytest.mark.asyncio
async def test_an_over_long_selection_entry_is_refused_at_the_boundary(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            "/api/v1/assets/5/bundle?scope_id=9000&model=m&selected_file_ids="
            + ("9" * 41)
        )
    assert r.status_code == 422
