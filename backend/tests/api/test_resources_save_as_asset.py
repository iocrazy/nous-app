"""``POST /resources/{id}/save-as-asset`` — the real router over the real
service, with only the repositories faked (P6 ruling E).

The service is NOT a stub here, unlike ``tests/api/test_generated_router.py``.
The claims worth pinning are the ones the orchestration makes — find-or-mint,
one transaction, nothing left behind on failure — and a fake service would
assert them about itself. So the app gets a real ``GeneratedInboxService`` with
in-memory repos, and ``unit_of_work`` is replaced by a fake that really does
undo the fake repo's writes when the block raises. That fake is what makes
"a failure at attach leaves no minted row" an OBSERVABLE assertion rather than
a comment; the ordering assertion next to it pins the structural property that
makes Postgres do the same thing in production.

Row fixtures copy ``GeneratedMediaRepository._normalize``: snowflake ids as
STRINGS, ``created_at`` as a driver ``datetime`` object.
"""

from __future__ import annotations

import copy
import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from app.api import assets_router as ar
from app.api import generated_router as gr
from app.api import resources_assets_router as rar
from app.core.deps import get_auth
from app.repositories.generated_media_repository import (
    GeneratedMediaRepository,
    _registration_lock_stmt,
)
from app.schemas.generated import SaveAsAssetRequest
from app.services.assets.assets_service import AssetError
from app.services.assets.slots import SLOTS
from app.services.library import generated_inbox_service as mod
from app.services.library.generated_inbox_service import GeneratedInboxService
from app.services.library.generated_source import LIBRARY_UPLOAD_ORIGIN

USER = "11111111-1111-1111-1111-111111111111"
OTHER_USER = "22222222-2222-2222-2222-222222222222"
SCOPE = "727145299382534200"
OTHER_SCOPE = "727145299382534299"
RESOURCE = "600000000000000001"
GEN = "800000000000000001"
MINTED = "800000000000000777"
ASSET = "700000000000000001"
CANVAS = "900000000000000001"
NOW = datetime.datetime(2026, 9, 4, 12, 0, tzinfo=datetime.timezone.utc)


class _AuthStub:
    user_id = USER


def make_resource(**kw) -> dict:
    row = {
        "id": RESOURCE,
        "creator_id": USER,
        "filename": "harbour.png",
        "mime_type": "image/png",
        "file_path": "t727145299382534200/ab/abcdef.png",
        "media_id": None,
        "source_type": "upload",
    }
    row.update(kw)
    return row


def make_gen_row(**kw) -> dict:
    row = {
        "id": GEN,
        "scope_id": SCOPE,
        "creator_id": USER,
        "media_kind": "image",
        "mime": "image/png",
        "file_path": "t727145299382534200/ab/abcdef.png",
        "file_size_bytes": 20481,
        "origin_kind": LIBRARY_UPLOAD_ORIGIN,
        "origin_run_id": None,
        "agent_id": None,
        "canvas_id": None,
        "node_id": None,
        "prompt": None,
        "model": None,
        "provider": None,
        "params": {},
        "cost_cents": None,
        "parent_resource_id": None,
        "derivation_kind": None,
        "promoted_resource_id": RESOURCE,
        "review_state": "saved",
        "source_asset_id": None,
        "conversation_id": None,
        "created_at": NOW,
    }
    row.update(kw)
    return row


# ── fakes ──────────────────────────────────────────────────────────────────


class FakeResources:
    """``ResourcesRepository`` — only the one visibility-checked read."""

    def __init__(self, rows=None):
        # ``rows or [...]`` would be WRONG: an EMPTY list is falsy, so the
        # "no such resource" fixture would silently get the default row back
        # and the test would pass for the wrong reason.
        rows = [make_resource()] if rows is None else rows
        self.rows = {str(r["id"]): r for r in rows}
        self.calls: list[tuple[str, str]] = []

    async def get_resource_by_id_for_caller(self, resource_id, user_id):
        self.calls.append((str(resource_id), str(user_id)))
        row = self.rows.get(str(resource_id))
        # Mirrors the real method: None for BOTH "no such row" and "not yours".
        if row is None or str(row["creator_id"]) != str(user_id):
            return None
        return row


class FakeGenRepo:
    """``GeneratedMediaRepository`` — find-or-mint plus the state write.

    ``insert_registered_resource`` reproduces the real one's contract: keyed on
    ``promoted_resource_id``, returns the EXISTING row rather than a duplicate,
    and a minted row is born ``saved`` (the state flip to ``in_assets`` is the
    save's last step, not the mint's).
    """

    def __init__(self, rows=None, log=None):
        self.log = log if log is not None else []
        self.rows: dict[int, dict] = {int(r["id"]): r for r in (rows or [])}
        self.inserts: list[dict] = []
        self.locked: list[int] = []
        self.next_id = MINTED

    async def lock_resource_registration(self, resource_id):
        self.log.append("lock_resource_registration")
        self.locked.append(int(resource_id))

    async def insert_registered_resource(self, **kw):
        self.log.append("insert_registered_resource")
        self.inserts.append(kw)
        for r in sorted(self.rows.values(), key=lambda r: int(r["id"])):
            if str(r.get("promoted_resource_id")) == str(kw["resource_id"]):
                return r
        row = make_gen_row(
            id=self.next_id,
            scope_id=str(kw["scope_id"]),
            creator_id=kw["creator_id"],
            media_kind=kw["media_kind"],
            mime=kw["mime"],
            file_path=kw["file_path"],
            origin_kind=kw["origin_kind"],
            conversation_id=kw["conversation_id"],
            params=dict(kw.get("params") or {}),
            promoted_resource_id=str(kw["resource_id"]),
            review_state="saved",
        )
        self.rows[int(row["id"])] = row
        return row

    async def get(self, gen_id, scope_id):
        r = self.rows.get(int(gen_id))
        return r if r and int(r["scope_id"]) == int(scope_id) else None

    async def set_review_state(self, gen_id, scope_id, state):
        self.log.append(f"set_review_state:{state}")
        r = self.rows.get(int(gen_id))
        if not r or int(r["scope_id"]) != int(scope_id):
            return None
        r["review_state"] = state
        return r


class FakeAssets:
    def __init__(self, attach_raises=None, require_raises=None, log=None):
        self.attach_raises = attach_raises
        self.require_raises = require_raises
        self.log = log if log is not None else []
        self.attached: list[tuple] = []
        self.created: list[tuple] = []

    async def _require_writable(self, asset_id, scope_id):
        self.log.append("require_writable")
        if self.require_raises:
            raise self.require_raises
        return {"id": ASSET, "asset_type": "character", "is_system_preset": False}

    async def create_asset(self, scope_id, payload, user_id):
        self.log.append("create_asset")
        self.created.append((int(scope_id), payload, user_id))
        return {"id": ASSET, "name": payload.name, "asset_type": payload.asset_type}

    async def attach_file(self, asset_id, scope_id, req, user_id):
        self.log.append("attach_file")
        if self.attach_raises:
            raise self.attach_raises
        self.attached.append((int(asset_id), int(scope_id), req, user_id))
        return {"id": "500000000000000001", "resource_id": req.resource_id}


class FakeCanvases:
    async def names_by_ids(self, ids):
        return {CANVAS: "Harbour Board"}


class FakeUnitOfWork:
    """A transaction that really rolls the fake repo back.

    ``__aenter__`` deep-copies the repo's rows; an exception on exit restores
    them. Ordering is recorded in the SHARED log so a test can also assert that
    the mint happened INSIDE the block — the structural property that makes the
    real Postgres transaction behave the same way.
    """

    def __init__(self, repo, log):
        self.repo = repo
        self.log = log
        self.enters = 0
        self.exit_excs: list = []
        self._snapshot: dict = {}

    def __call__(self):
        return self

    async def __aenter__(self):
        self.enters += 1
        self.log.append("uow_enter")
        self._snapshot = copy.deepcopy(self.repo.rows)
        return None

    async def __aexit__(self, exc_type, exc, tb):
        self.exit_excs.append(exc_type)
        if exc_type is not None:
            self.repo.rows = self._snapshot
            self.log.append("uow_rollback")
        else:
            self.log.append("uow_commit")
        return False


def build_app(monkeypatch, *, gen_rows=None, resources=None, assets=None):
    log: list[str] = []
    repo = FakeGenRepo(gen_rows or [], log=log)
    res = resources if resources is not None else FakeResources()
    ast = assets or FakeAssets()
    ast.log = log
    uow = FakeUnitOfWork(repo, log)
    monkeypatch.setattr(mod, "unit_of_work", uow)

    svc = GeneratedInboxService(
        gen_repo=repo,
        promote=object(),  # never reached on this path — see the service docstring
        assets=ast,
        canvases=FakeCanvases(),
        resources=res,
    )

    application = FastAPI()
    application.include_router(rar.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    async def _member_ok(scope_id, user_id):
        return str(scope_id) == SCOPE

    application.dependency_overrides[get_auth] = _fake_auth
    # ``_gate`` is imported from assets_router, so the membership check has to be
    # patched where it is DEFINED — patching a re-export would leave the real
    # query running and the test would silently hit the database.
    monkeypatch.setattr(ar, "_is_member", _member_ok)
    monkeypatch.setattr(rar, "_service", lambda: svc)

    application.state.repo = repo
    application.state.res = res
    application.state.assets = ast
    application.state.uow = uow
    application.state.log = log
    return application


@pytest.fixture
def app(monkeypatch):
    return build_app(monkeypatch)


async def _post(application, body=None, resource_id=RESOURCE, scope=SCOPE):
    body = body if body is not None else {"asset_id": ASSET, "slot": "sheet"}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://t"
    ) as c:
        return await c.post(
            f"/api/v1/resources/{resource_id}/save-as-asset?scope_id={scope}",
            json=body,
        )


# ── mint path ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mints_an_inbox_row_and_saves_it_as_an_asset(app):
    """A plain My Uploads file has no inbox row; the save has to make one."""
    r = await _post(app)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert data["asset_id"] == ASSET
    assert data["resource_id"] == RESOURCE
    assert data["generated_id"] == MINTED
    assert data["generation"]["review_state"] == "in_assets"
    # The row really exists, really registers THIS resource, and really ended
    # up in_assets — not merely reported as such.
    row = app.state.repo.rows[int(MINTED)]
    assert row["promoted_resource_id"] == RESOURCE
    assert row["review_state"] == "in_assets"
    assert row["origin_kind"] == LIBRARY_UPLOAD_ORIGIN
    assert app.state.assets.attached[0][2].resource_id == RESOURCE


@pytest.mark.asyncio
async def test_the_minted_row_carries_the_resources_own_file_and_owner(app):
    """No blob is copied: the row points at the resource's stored path, and
    ``creator_id`` describes who owns the file, not who clicked the menu."""
    await _post(app)
    kw = app.state.repo.inserts[0]
    assert kw["file_path"] == make_resource()["file_path"]
    assert kw["creator_id"] == USER
    assert kw["media_kind"] == "image"
    assert kw["conversation_id"] is None
    assert kw["scope_id"] == int(SCOPE)


@pytest.mark.asyncio
async def test_the_minted_row_is_titled_after_the_file(app):
    """A registered resource has no generation prompt, so ``derive_title``
    used to fall through to ``f"{media_kind} · {origin_kind}"`` — an mp3 saved
    through "As Asset" read **audio · library_upload**, naming neither the file
    nor anything the user wrote. The file's own stem is the title source.

    Asserted on the RESPONSE's derived title, not only on the mint kwarg: the
    kwarg alone would pass even if ``derive_title`` ignored the new source."""
    r = await _post(app)
    assert r.status_code == 201, r.text
    assert app.state.repo.inserts[0]["params"] == {"source_filename": "harbour"}
    assert r.json()["data"]["generation"]["title"] == "harbour"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("song.mp3", "song"),
        # Only the LAST suffix goes: guessing at double extensions would eat
        # a real part of the name. The dot that SURVIVES is the reason the name
        # travels in ``params`` and not in ``prompt`` — ``derive_title`` cuts a
        # prompt at its first ``.``, so this row would have been titled
        # "stems" through that route.
        ("stems.tar.gz", "stems.tar"),
        # A leading-dot name is all name and no extension. Through ``prompt``
        # it would have cut to nothing and taken the fallback.
        (".gitignore", ".gitignore"),
        # No dot at all.
        ("masterlist", "masterlist"),
        # Dots inside the stem are the common real case, not an edge one.
        ("interview.v2.mp3", "interview.v2"),
    ],
)
async def test_the_extension_is_stripped_but_the_name_is_not(
    monkeypatch, filename, expected
):
    application = build_app(
        monkeypatch,
        resources=FakeResources(
            [make_resource(filename=filename, mime_type="audio/mpeg")]
        ),
    )
    r = await _post(application)
    assert r.status_code == 201, r.text
    assert application.state.repo.inserts[0]["params"] == {"source_filename": expected}
    assert r.json()["data"]["generation"]["title"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["", "   ", None])
async def test_a_nameless_resource_keeps_the_descriptive_fallback(
    monkeypatch, filename
):
    """No key at all, never an empty one: with nothing to name the row after,
    ``derive_title`` should reach its descriptive fallback rather than title
    the card the empty string, which renders as a blank card."""
    application = build_app(
        monkeypatch,
        resources=FakeResources(
            [make_resource(filename=filename, mime_type="audio/mpeg")]
        ),
    )
    r = await _post(application)
    assert r.status_code == 201, r.text
    assert application.state.repo.inserts[0]["params"] == {}
    assert r.json()["data"]["generation"]["title"] == "audio · library_upload"


@pytest.mark.asyncio
async def test_new_asset_variant_creates_then_attaches(app):
    r = await _post(
        app,
        body={
            "new_asset": {"asset_type": "character", "name": "Mara"},
            "slot": "sheet",
        },
    )
    assert r.status_code == 201, r.text
    assert app.state.assets.created and app.state.assets.attached
    assert app.state.log.index("create_asset") > app.state.log.index("uow_enter")


# ── reuse / idempotency ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reuses_the_existing_inbox_row(monkeypatch):
    """A chat upload or an earlier promote already left a row — a second one
    would break the "one inbox row per resource" invariant the repo relies on."""
    application = build_app(
        monkeypatch, gen_rows=[make_gen_row(id=GEN, origin_kind="chat_upload")]
    )
    r = await _post(application)
    assert r.status_code == 201, r.text
    assert r.json()["data"]["generated_id"] == GEN
    assert set(application.state.repo.rows) == {int(GEN)}
    assert application.state.repo.rows[int(GEN)]["review_state"] == "in_assets"


@pytest.mark.asyncio
async def test_second_call_is_idempotent(app):
    first = await _post(app)
    second = await _post(app)
    assert first.status_code == 201 and second.status_code == 201, second.text
    assert first.json()["data"]["generated_id"] == second.json()["data"]["generated_id"]
    # One row, not two — the second call hit the find, not the mint.
    assert len(app.state.repo.rows) == 1
    assert len(app.state.repo.inserts) == 2  # both calls asked; only one minted


# ── one transaction ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_attach_leaves_no_minted_row(monkeypatch):
    """The reason the mint is inside the transaction at all.

    Without it a user whose save fails (or who cancels after one) finds an
    orphan card in the Generated inbox for a file they never generated.
    """
    application = build_app(
        monkeypatch,
        assets=FakeAssets(
            attach_raises=AssetError(409, "attach_conflict", "slot taken")
        ),
    )
    r = await _post(application)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "attach_conflict"
    assert application.state.repo.rows == {}
    log = application.state.log
    # Structural claim: the mint is INSIDE the block that rolled back.
    assert log.index("uow_enter") < log.index("insert_registered_resource")
    assert log.index("insert_registered_resource") < log.index("attach_file")
    assert log[-1] == "uow_rollback"
    assert application.state.uow.exit_excs == [AssetError]


@pytest.mark.asyncio
async def test_validation_refuses_before_the_transaction_opens(monkeypatch):
    """An unknown/read-only asset is knowable from a cheap read, so it must not
    cost a transaction — nor leave a minted row behind a 404."""
    application = build_app(
        monkeypatch,
        assets=FakeAssets(
            require_raises=AssetError(404, "asset_not_found", "No such asset")
        ),
    )
    r = await _post(application)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "asset_not_found"
    assert application.state.uow.enters == 0
    assert application.state.repo.rows == {}


# ── typed refusals ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", ["video/mp4", "application/pdf", "", "text/plain"])
async def test_a_kind_no_slot_accepts_is_a_typed_422(monkeypatch, mime):
    """Nothing in the slot table takes a video or a document, so attaching one
    would produce a broken card rather than a saved asset."""
    application = build_app(
        monkeypatch,
        resources=FakeResources([make_resource(mime_type=mime)]),
    )
    r = await _post(application)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "resource_kind_unsupported"
    # The detail NAMES what would work — a refusal the user cannot act on is
    # half a refusal.
    assert "image" in err["detail"] and "audio" in err["detail"]
    assert application.state.uow.enters == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime", "kind"),
    [("image/png", "image"), ("audio/mpeg", "audio"), ("audio/wav", "audio")],
)
async def test_image_and_audio_are_both_accepted(monkeypatch, mime, kind):
    """``audio`` assets are real: their ``primary`` / ``variants`` slots take
    an audio file, so refusing audio would make the menu lie about a supported
    asset type. The minted row carries the matching ``media_kind`` — writing
    "image" for an audio file would mislabel it in the inbox forever."""
    application = build_app(
        monkeypatch, resources=FakeResources([make_resource(mime_type=mime)])
    )
    r = await _post(application)
    assert r.status_code == 201, r.text
    assert application.state.repo.inserts[0]["media_kind"] == kind
    assert application.state.repo.inserts[0]["mime"] == mime


def test_the_accepted_kinds_are_exactly_what_the_slot_table_supports():
    """A TRIPWIRE, not a derivation — and the difference matters.

    "Every non-audio type's slots take a visual reference, audio's take an
    audio file" is not encoded anywhere: the slot table names slots, not the
    file shapes they accept. So both halves below are retyped literals, and
    the step between them is human reasoning this test cannot check.

    What it DOES buy: widening ``ACCEPTED_ASSET_FILE_KINDS``, or adding a
    seventh asset type, fails HERE and forces someone to redo that reasoning
    rather than discovering it as a 422 the user cannot explain. Making it a
    real derivation means annotating each slot with the file shape it accepts
    — a change to ``slots.py``, which is mirrored into the frontend and pinned
    by ``test_slots_frontend_mirror.py``; out of scope here.
    """
    assert mod.ACCEPTED_ASSET_FILE_KINDS == ("image", "audio")
    assert set(SLOTS) - {"prompt"} == {
        "character",
        "location",
        "prop",
        "costume",
        "audio",
    }


@pytest.mark.asyncio
async def test_resource_with_no_single_file_is_a_typed_422(monkeypatch):
    """An album resolves to a DIRECTORY prefix, and a row that was never
    downloaded resolves to nothing — both are ``resource_file_unresolved``, not
    a save that silently attaches a path nothing can read.

    The code is deliberately NOT ``materialize_failed``: that one tells the
    user to retry, and neither of these two causes can ever succeed on a retry.
    The user-facing copy has to name both causes for the same reason."""
    application = build_app(
        monkeypatch,
        resources=FakeResources([make_resource(file_path="", media_id=None)]),
    )
    r = await _post(application)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "resource_file_unresolved"
    assert application.state.uow.enters == 0


@pytest.mark.asyncio
async def test_album_directory_prefix_is_file_unresolved(monkeypatch):
    application = build_app(
        monkeypatch,
        resources=FakeResources(
            [make_resource(file_path="t727145299382534200/album/600000000000000001/")]
        ),
    )
    r = await _post(application)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "resource_file_unresolved"


@pytest.mark.asyncio
async def test_resource_of_another_user_is_a_typed_404(monkeypatch):
    """ "Not yours" and "not there" answer the same, on purpose: the caller must
    not learn which from the status."""
    application = build_app(
        monkeypatch, resources=FakeResources([make_resource(creator_id=OTHER_USER)])
    )
    r = await _post(application)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "resource_not_accessible"
    assert application.state.repo.rows == {}


@pytest.mark.asyncio
async def test_unknown_resource_is_the_same_typed_404(monkeypatch):
    application = build_app(monkeypatch, resources=FakeResources([]))
    r = await _post(application)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "resource_not_accessible"


@pytest.mark.asyncio
async def test_a_scope_the_caller_does_not_belong_to_is_403(app):
    r = await _post(app, scope=OTHER_SCOPE)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_a_member"
    # The gate runs before the resource is even read.
    assert app.state.res.calls == []


@pytest.mark.asyncio
async def test_a_body_naming_both_targets_is_422(app):
    r = await _post(
        app,
        body={
            "asset_id": ASSET,
            "new_asset": {"asset_type": "character", "name": "Mara"},
            "slot": "sheet",
        },
    )
    assert r.status_code == 422


# ── concurrency: the mint is serialised per resource ───────────────────────


@pytest.mark.asyncio
async def test_the_registration_lock_is_taken_before_the_lookup(app):
    """``insert_registered_resource`` is idempotent by a SELECT, not by a
    unique index, and this endpoint is the first writer a user can fire twice.
    The lock has to be inside the transaction and BEFORE the find-or-mint, or
    two concurrent submits both read "no row" and both insert — leaving a
    second, permanently unreachable ``saved`` card.
    """
    r = await _post(app)
    assert r.status_code == 201, r.text
    log = app.state.log
    assert log.index("uow_enter") < log.index("lock_resource_registration")
    assert log.index("lock_resource_registration") < log.index(
        "insert_registered_resource"
    )
    # Keyed on the RESOURCE, not the scope or the asset: the row it protects
    # is the one keyed on ``promoted_resource_id``.
    assert app.state.repo.locked == [int(RESOURCE)]


def test_the_repository_really_exposes_the_lock_the_fake_stands_in_for():
    assert callable(GeneratedMediaRepository().lock_resource_registration)


def test_the_lock_statement_is_a_namespaced_transaction_level_advisory_lock():
    """Compiled SQL, because every property here is invisible in Python.

    ``_xact_`` (not the session-level ``pg_advisory_lock``) is what makes the
    lock end with the transaction rather than leaking onto a pooled
    connection. The namespace prefix is what stops the key colliding with
    another module's per-id locks — ``hashtextextended`` of a bare snowflake
    would collide with anything else that hashes the same id.
    """
    sql = str(
        _registration_lock_stmt(int(RESOURCE)).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "pg_advisory_xact_lock(" in sql
    assert "pg_advisory_lock(" not in sql
    assert f"'generated_media_registration:{RESOURCE}'" in sql
    assert "hashtextextended(" in sql


# ── contract parity with the generation endpoint ───────────────────────────


def _route(application, path: str, method: str):
    return next(
        r
        for r in application.routes
        if getattr(r, "path", None) == path and method in getattr(r, "methods", ())
    )


def _generated_app():
    application = FastAPI()
    application.include_router(gr.router, prefix="/api/v1")
    return application


def test_both_endpoints_declare_the_SAME_request_model(app):
    """One model, not two that happen to agree today.

    ``SaveAsAssetDialog`` posts one body shape. A forked request model for the
    resource route would let the two drift — a new field added for the dialog
    would reach one endpoint and be rejected as ``extra="forbid"`` by the
    other, which reads to the user as "As Asset is broken from My Uploads".
    """
    mine = _route(app, "/api/v1/resources/{resource_id}/save-as-asset", "POST")
    theirs = _route(
        _generated_app(), "/api/v1/generated/{gen_id}/save-as-asset", "POST"
    )
    assert mine.body_field.type_ is theirs.body_field.type_
    assert mine.body_field.type_ is SaveAsAssetRequest
    assert mine.status_code == theirs.status_code == 201


@pytest.mark.asyncio
async def test_the_response_is_the_generation_shape_plus_generated_id(app):
    """Pinned as a SET difference, not a key list: a field dropped from either
    side fails here rather than as an undefined in the dialog."""
    r = await _post(app)
    assert r.status_code == 201, r.text
    got = set(r.json()["data"])
    generated_shape = {"generation", "asset_id", "resource_id"}
    assert got - generated_shape == {"generated_id"}
    assert generated_shape <= got
