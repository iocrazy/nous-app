"""``GET /assets/search`` — the chat @-picker's membership-wide asset shelf.

WHAT THIS FILE OWNS, AND WHAT IT DOES NOT
─────────────────────────────────────────
The route takes NO ``scope_id``, so unlike every other read on this router its
answer is decided by ruling B's predicate (membership in the asset's scope OR
``is_system_preset``, soft-deleted rows excluded) rather than by ``_gate``.
Four things can go wrong and only one of them is about SQL:

* the route is shadowed by ``/assets/{asset_id}`` (registration order),
* it searches as somebody other than the authenticated caller,
* a filter the server does not understand is ignored instead of refused, so a
  dropdown looks filtered and is not,
* the rows come back in a different shape than ``GET /assets`` sends, so the
  picker and the shelf render the same asset differently.

The visibility cases below are NOT served by a fake that decides them. The real
``AssetsRepository`` builds the real statement and it is EVALUATED against an
in-memory fixture by the clause interpreter ``test_asset_ref_resolver`` already
owns (imported, not forked — a second copy would be free to drift from the one
the ruling-B guard runs on). The interpreter refuses loudly anything it cannot
read, so a predicate that changes shape errors instead of passing.

Two things it cannot answer, and neither is left unproven elsewhere:

* the ILIKE escaping behind ``q`` — the interpreter has no LIKE. What this file
  pins is that the router hands the caller's term to the repository UNTOUCHED
  (the escape is ``_like_escape``'s job, one layer down); that the escape is
  emitted is pinned in ``tests/services/assets/test_assets_repository_sql.py``
  and that PostgreSQL then honours it in
  ``tests/db/test_assets_repository_integration.py`` (case 23).
* ``ORDER BY`` / ``LIMIT``, which the interpreter ignores by design — so the
  limit is pinned as the value that reaches the repository, not as a row count.

The three derived-count queries (``slot_counts`` / ``project_ids`` /
``loadout_counts``) are stubbed: they are plain GROUP BYs shared with the shelf
and proved against a real server by integration case 11.
"""

from __future__ import annotations

import datetime
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import assets_router as ar
from app.core.deps import get_auth
from app.models import Assets, TeamMembers
from app.models.assets import ASSET_TYPES
from app.repositories.assets_repository import AssetsRepository
from app.schemas.assets import AssetResponse, Envelope

# The generic clause interpreter from the ruling-B guard. Imported so both
# suites judge the SAME production statement with the SAME evaluator.
from tests.services.ai.chat.test_asset_ref_resolver import _Session

pytestmark = [pytest.mark.unit]

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "22222222-2222-2222-2222-222222222222"
TEAM_A = 700000000000000001
TEAM_B = 700000000000000002
TEAM_OUT = 700000000000000003

# Past 2**53: a wire id that JSON-number rounding would visibly corrupt, so the
# ``str()`` at the boundary is asserted against a value where it matters.
MINE = 727145299382534146
OTHERS = 727145299382534147
PRESET = 727145299382534148
DELETED = 727145299382534149

NOW = datetime.datetime(2026, 9, 3, 12, 0, tzinfo=datetime.timezone.utc)


class _AuthStub:
    user_id = USER


def asset_row(asset_id: int, **over) -> Dict[str, Any]:
    """One ``assets`` row in NATIVE types — every mapped column, because
    ``select(Assets)`` names every one of them and the response model rejects a
    ``None`` where the real column is NOT NULL. CLAUDE.md 边界 mock 必须用真实
    JSON 形状, one layer earlier: this is the row Postgres would hand back, and
    ``_serialize`` is what turns it into wire shape.
    """
    row = {
        "id": asset_id,
        "scope_id": TEAM_A,
        "asset_type": "character",
        "subtype": None,
        "name": f"Asset {asset_id}",
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
        "in_library": True,
        "tags": {},
        "sort_order": 0,
        "created_by": uuid.UUID(USER),
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    row.update(over)
    return row


@pytest.fixture
def db() -> Dict[str, List[Dict[str, Any]]]:
    """One asset per visibility case, plus the membership table the predicate
    joins through. ``OTHER_USER`` belongs to ``TEAM_OUT`` and the caller does
    not — the negative case is a real row somebody else can see, not an absent
    one."""
    return {
        "assets": [
            asset_row(MINE, scope_id=TEAM_A, name="Sang Yao"),
            asset_row(OTHERS, scope_id=TEAM_OUT, name="Not Mine"),
            asset_row(
                PRESET,
                scope_id=None,
                is_system_preset=True,
                name="Preset Hero",
                cover_file_id=727145299382534200,
            ),
            asset_row(DELETED, scope_id=TEAM_A, name="Erased", deleted_at=NOW),
        ],
        # Real ``uuid.UUID`` objects — the column is ``uuid`` and the
        # repository normalises the bound value, so a string-keyed fixture
        # would compare two things the server never compares.
        "team_members": [
            {"user_id": uuid.UUID(USER), "team_id": TEAM_A},
            {"user_id": uuid.UUID(USER), "team_id": TEAM_B},
            {"user_id": uuid.UUID(OTHER_USER), "team_id": TEAM_OUT},
        ],
    }


@pytest.fixture(autouse=True)
def wire(monkeypatch, db):
    import app.repositories.assets_repository as assets_module

    @asynccontextmanager
    async def _read_scope():
        yield _Session(db, {"assets": Assets, "team_members": TeamMembers})

    monkeypatch.setattr(assets_module, "read_scope", _read_scope)

    async def _no_counts(self, ids):
        return {}

    for name in ("slot_counts", "project_ids", "loadout_counts"):
        monkeypatch.setattr(AssetsRepository, name, _no_counts)


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(ar.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    application.dependency_overrides[get_auth] = _fake_auth
    return application


@asynccontextmanager
async def _client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _search(app, qs: str = ""):
    async with _client(app) as c:
        return await c.get(f"/api/v1/assets/search{qs}")


async def _names(app, qs: str = ""):
    r = await _search(app, qs)
    assert r.status_code == 200, r.text
    return {row["name"] for row in r.json()["data"]}


# ── visibility (ruling B, evaluated against the real statement) ─────────────


async def test_a_member_sees_their_teams_asset(app):
    assert "Sang Yao" in await _names(app)


async def test_another_teams_asset_is_invisible(app):
    """The row exists and OTHER_USER can see it; the caller is not in that
    team, so it must not be on this shelf. A picker that offered it would
    hand the chat an asset the resolver then refuses to attach."""
    assert "Not Mine" not in await _names(app)


async def test_a_system_preset_is_visible_from_no_scope_at_all(app):
    """Presets carry ``scope_id NULL`` — the membership arm can never match
    one, so they are visible only because of the explicit ``is_system_preset``
    arm. Dropping that arm is a silent loss, not an error."""
    assert "Preset Hero" in await _names(app)


async def test_a_soft_deleted_asset_is_absent(app):
    assert "Erased" not in await _names(app)


async def test_the_type_filter_narrows_rather_than_being_ignored(app, db):
    db["assets"].append(
        asset_row(727145299382534150, asset_type="prop", name="Jade Dagger")
    )
    assert await _names(app, "?type=prop") == {"Jade Dagger"}
    assert "Jade Dagger" not in await _names(app, "?type=character")


# ── wire shape ─────────────────────────────────────────────────────────────


async def test_ids_ride_as_strings_not_json_numbers(app):
    r = await _search(app)
    rows = {row["name"]: row for row in r.json()["data"]}
    mine, preset = rows["Sang Yao"], rows["Preset Hero"]

    assert mine["id"] == str(MINE) and isinstance(mine["id"], str)
    assert mine["scope_id"] == str(TEAM_A) and isinstance(mine["scope_id"], str)
    assert preset["cover_file_id"] == "727145299382534200"
    # A preset belongs to no scope, and null is the honest answer — "0" or ""
    # would read as a team.
    assert preset["scope_id"] is None


def _route(app, path: str, method: str = "GET"):
    return next(
        r
        for r in app.routes
        if getattr(r, "path", None) == path and method in getattr(r, "methods", ())
    )


def test_the_search_card_is_the_shelf_card_MODEL(app):
    """One model, not two that happen to agree today.

    Reading the serialized body and comparing its keys to
    ``AssetResponse.model_fields`` proves nothing: FastAPI built that body FROM
    that model, so the check passes whatever the model says — including after
    someone forks a narrower ``AssetSearchResponse`` for this route and the
    picker quietly starts rendering a different card than the shelf. The claim
    worth pinning is that both routes answer with the SAME declared model.
    """
    search = _route(app, "/api/v1/assets/search")
    shelf = _route(app, "/api/v1/assets")

    assert search.response_model is shelf.response_model

    meta = search.response_model.__pydantic_generic_metadata__
    assert meta["origin"] is Envelope
    assert meta["args"] == (List[AssetResponse],)
    # The two the picker specifically needs (P5 ruling G), named so dropping
    # either from the shared model fails HERE and not in a component test.
    assert {"scope_id", "cover_file_id"} <= set(AssetResponse.model_fields)


async def test_the_service_really_emits_the_declared_card(app):
    """The model says what the wire MAY carry; this says what it DID. A field
    the service stopped emitting would still validate (both are optional) and
    reach the picker as an undefined."""
    card = (await _search(app)).json()["data"][0]
    assert set(card) == set(AssetResponse.model_fields)
    assert card["readiness"]["state"] in ("ready", "draft")


async def test_success_wears_the_envelope(app):
    body = (await _search(app)).json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


# ── routing, identity and parameter validation ─────────────────────────────


async def test_search_is_not_captured_as_an_asset_id(app):
    """``/assets/{asset_id}`` is an ``int`` path param, so if it were
    registered first this request would answer 422 about an id nobody sent —
    the same trap ``/assets/counts`` and ``/assets/resolve-legacy`` document.
    """
    r = await _search(app)
    assert r.status_code == 200, r.text

    paths = [getattr(rt, "path", "") for rt in app.routes]
    assert paths.index("/api/v1/assets/search") < paths.index(
        "/api/v1/assets/{asset_id}"
    )


async def test_the_search_runs_as_the_authenticated_user(app, monkeypatch):
    seen: List[Dict[str, Any]] = []

    async def _spy(self, user_id, **kw):
        seen.append({"user_id": user_id, **kw})
        return []

    monkeypatch.setattr(AssetsRepository, "list_accessible", _spy)
    # A caller-supplied identity must not be able to steer the read; the route
    # declares no such parameter, so this rides as an ignored extra.
    r = await _search(app, f"?user_id={OTHER_USER}&scope_id={TEAM_OUT}")

    assert r.status_code == 200
    assert seen[0]["user_id"] == USER
    assert "scope_id" not in seen[0]


async def test_defaults_and_the_search_term_reach_the_repository_verbatim(
    app, monkeypatch
):
    """``q`` is forwarded UNCHANGED — LIKE escaping belongs to ``_like_escape``
    one layer down, and a router that pre-mangled the term would escape it
    twice. ``library`` defaults to ``all`` (the shelf's ``in`` would hide every
    script-imported character from the picker) and ``limit`` to 24.

    ``include_deleted`` must never be passed: the repository has it so a caller
    holding an id can tell "deleted" from "not yours", and a SEARCH that used
    it would put erased rows back on a shelf the user emptied.
    """
    seen: List[Dict[str, Any]] = []

    async def _spy(self, user_id, **kw):
        seen.append(kw)
        return []

    monkeypatch.setattr(AssetsRepository, "list_accessible", _spy)
    assert (await _search(app, "?q=a_b%25")).status_code == 200

    assert seen[0]["q"] == "a_b%"
    assert seen[0]["library"] == "all"
    assert seen[0]["limit"] == 24
    assert seen[0]["asset_type"] is None
    assert "include_deleted" not in seen[0]


async def test_explicit_filters_reach_the_repository(app, monkeypatch):
    seen: List[Dict[str, Any]] = []

    async def _spy(self, user_id, **kw):
        seen.append(kw)
        return []

    monkeypatch.setattr(AssetsRepository, "list_accessible", _spy)
    assert (await _search(app, "?type=prompt&library=out&limit=50")).status_code == 200

    assert seen[0]["asset_type"] == "prompt"
    assert seen[0]["library"] == "out"
    assert seen[0]["limit"] == 50


@pytest.mark.parametrize("asset_type", list(ASSET_TYPES))
async def test_every_asset_type_the_model_knows_is_accepted(app, asset_type):
    """The pattern is built FROM ``ASSET_TYPES``. A seventh type added to the
    model but not to the route would answer 422 for a type the product
    supports — a refusal that reads like a client bug."""
    assert (await _search(app, f"?type={asset_type}")).status_code == 200


@pytest.mark.parametrize(
    "qs",
    [
        "?type=weapon",
        "?type=",
        "?library=everything",
        "?limit=51",
        "?limit=0",
    ],
)
async def test_a_filter_the_server_cannot_honour_is_refused(app, qs):
    """422, never a quiet fallback: a value the server ignored returns the
    whole shelf looking filtered."""
    assert (await _search(app, qs)).status_code == 422
