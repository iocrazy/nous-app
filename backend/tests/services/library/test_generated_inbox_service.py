"""GeneratedInboxService invariants with in-memory fakes (no DB).

Same shape as ``tests/services/assets/test_assets_service.py``: the repos and
the collaborating services are fakes, so what is under test is the
orchestration — error mapping, the save-as-asset ordering inside one unit of
work, and the per-item accounting of a batch.

Row fixtures copy what ``GeneratedMediaRepository._normalize`` actually
returns: snowflake ids as STRINGS, ``created_at`` as a driver ``datetime``.
(The wire-shape variant — an ISO string — is covered by the contract test at
the bottom.)
"""

from __future__ import annotations

import datetime

import pytest

from app.repositories.generated_media_repository import CLEANUP_SCAN_LIMIT
from app.schemas.generated import (
    BatchRequest,
    CleanupRequest,
    NewAssetSpec,
    SaveAsAssetRequest,
)
from app.services.assets.assets_service import AssetError
from app.services.library import generated_inbox_service as mod
from app.services.library.generated_inbox_service import GeneratedInboxService

NOW = datetime.datetime(2026, 8, 20, 12, 0, tzinfo=datetime.timezone.utc)
SCOPE = 727145299382534200
USER = "11111111-1111-1111-1111-111111111111"
CANVAS = "900000000000000001"
GEN = "800000000000000001"
ASSET = "700000000000000001"
RESOURCE = "600000000000000001"


def make_row(**kw) -> dict:
    base = {
        "id": GEN,
        "scope_id": str(SCOPE),
        "creator_id": USER,
        "media_kind": "image",
        "mime": "image/png",
        "file_path": "sb://generated/ab/abcdef.png",
        "file_size_bytes": 20481,
        "origin_kind": "canvas_run",
        "origin_run_id": None,
        "agent_id": None,
        "canvas_id": CANVAS,
        "node_id": "node-7",
        "prompt": "A wide shot of the harbour. Golden hour, long lens.",
        "model": "doubao-seedream",
        "provider": "volcengine",
        "params": {},
        "cost_cents": 3,
        "parent_resource_id": None,
        "derivation_kind": None,
        "promoted_resource_id": None,
        "review_state": "unreviewed",
        "source_asset_id": None,
        "conversation_id": None,
        "created_at": NOW,
    }
    base.update(kw)
    return base


# ── fakes ──────────────────────────────────────────────────────────────────


class FakeGenRepo:
    def __init__(self, rows=None, log=None):
        self.log = log if log is not None else []
        self.rows: dict[int, dict] = {int(r["id"]): r for r in (rows or [])}
        self.deleted: list[int] = []
        self.state_writes: list[tuple[int, int, str]] = []
        self.delete_returns: dict[int, bool] = {}

    async def list_inbox(self, scope_id, **filters):
        self.last_filters = dict(filters)
        rows = [r for r in self.rows.values() if int(r["scope_id"]) == int(scope_id)]
        return {"items": rows, "next_cursor": "cur-2"}

    async def get(self, gen_id, scope_id):
        r = self.rows.get(int(gen_id))
        return r if r and int(r["scope_id"]) == int(scope_id) else None

    async def delete(self, gen_id, scope_id):
        self.deleted.append(int(gen_id))
        if int(gen_id) in self.delete_returns:
            return self.delete_returns[int(gen_id)]
        return self.rows.pop(int(gen_id), None) is not None

    async def set_review_state(self, gen_id, scope_id, state):
        self.log.append(f"set_review_state:{state}")
        self.state_writes.append((int(gen_id), int(scope_id), state))
        r = self.rows.get(int(gen_id))
        if not r or int(r["scope_id"]) != int(scope_id):
            return None
        r["review_state"] = state
        return r

    async def count_by_state(self, scope_id):
        return {"unreviewed": 4, "saved": 2, "in_assets": 1, "deleted": 9}

    async def list_older_unreviewed(self, scope_id, older_than, limit=500):
        self.cleanup_call = (int(scope_id), older_than, limit)
        return [
            r
            for r in self.rows.values()
            if r["review_state"] == "unreviewed" and r["created_at"] < older_than
        ]


class FakePromote:
    """Stands in for PromoteGeneratedMediaService (returns a resource row)."""

    def __init__(self, raises=None, log=None):
        self.raises = raises
        self.log = log if log is not None else []
        self.calls: list[dict] = []

    async def promote(self, *, gen_id, user_id, target_scope_id):
        self.log.append("promote")
        self.calls.append(
            {"gen_id": gen_id, "user_id": user_id, "target_scope_id": target_scope_id}
        )
        if self.raises:
            raise self.raises
        row = self.rows.get(int(gen_id)) if hasattr(self, "rows") else None
        if row is not None:
            row["promoted_resource_id"] = RESOURCE
            row["review_state"] = "saved"
        return {"id": RESOURCE, "filename": "abcdef.png"}


class FakeAssets:
    """Stands in for ``AssetsService``.

    ``_require_writable`` is modelled too: ``_validate_target`` calls the real
    service's own gate rather than reimplementing it, so the fake has to answer
    the same shape — the asset row, with the ``asset_type`` the slot table is
    keyed on. Slots in this file are REAL slots (``app/services/assets/slots``
    is imported for real by the service under test); a made-up slot name here
    would be a fixture that the production code path rejects.
    """

    def __init__(
        self, attach_raises=None, create_raises=None, log=None, require_raises=None
    ):
        self.attach_raises = attach_raises
        self.create_raises = create_raises
        self.require_raises = require_raises
        self.log = log if log is not None else []
        self.created: list[tuple] = []
        self.attached: list[tuple] = []
        self.required: list[tuple] = []

    async def _require_writable(self, asset_id, scope_id):
        self.log.append("require_writable")
        if self.require_raises:
            raise self.require_raises
        self.required.append((int(asset_id), int(scope_id)))
        return {"id": ASSET, "asset_type": "character", "is_system_preset": False}

    async def create_asset(self, scope_id, payload, user_id):
        self.log.append("create_asset")
        if self.create_raises:
            raise self.create_raises
        self.created.append((int(scope_id), payload, user_id))
        return {"id": ASSET, "name": payload.name, "asset_type": payload.asset_type}

    async def attach_file(self, asset_id, scope_id, req, user_id):
        self.log.append("attach_file")
        if self.attach_raises:
            raise self.attach_raises
        self.attached.append((int(asset_id), int(scope_id), req, user_id))
        return {"id": "500000000000000001", "resource_id": req.resource_id}


class FakeCanvases:
    def __init__(self, names=None):
        self.names = names or {CANVAS: "Harbour Board"}
        self.calls: list[list[int]] = []

    async def names_by_ids(self, ids):
        self.calls.append(list(ids))
        return {k: v for k, v in self.names.items() if int(k) in {int(i) for i in ids}}


class FakeUnitOfWork:
    """``async with unit_of_work():`` recorder — counts enters and exits."""

    def __init__(self):
        self.enters = 0
        self.exits = 0
        self.exit_excs: list = []
        # Shared ordering log — the fakes append to it too, so a test can
        # assert what ran INSIDE the transaction and what ran before it.
        self.log: list[str] = []

    def __call__(self):
        return self

    async def __aenter__(self):
        self.enters += 1
        self.log.append("uow_enter")
        return None

    async def __aexit__(self, exc_type, exc, tb):
        self.exits += 1
        self.exit_excs.append(exc_type)
        self.log.append("uow_exit" if exc_type is None else "uow_rollback")
        return False


def build(rows=None, promote=None, assets=None, canvases=None, log=None):
    log = log if log is not None else []
    repo = FakeGenRepo(rows if rows is not None else [make_row()], log=log)
    promote = promote or FakePromote()
    promote.log = log
    promote.rows = repo.rows
    assets = assets or FakeAssets()
    assets.log = log
    svc = GeneratedInboxService(
        gen_repo=repo,
        promote=promote,
        assets=assets,
        canvases=canvases or FakeCanvases(),
    )
    return svc, repo, promote


@pytest.fixture
def fake_uow(monkeypatch):
    uow = FakeUnitOfWork()
    monkeypatch.setattr(mod, "unit_of_work", uow)
    return uow


# ── list / counts ──────────────────────────────────────────────────────────


async def test_list_attaches_source_and_title_and_resolves_names_once():
    canvases = FakeCanvases()
    svc, _repo, _p = build(
        rows=[make_row(), make_row(id="800000000000000002")], canvases=canvases
    )
    page = await svc.list(SCOPE, str(SCOPE))
    assert page["next_cursor"] == "cur-2"
    assert len(page["items"]) == 2
    item = page["items"][0]
    assert item["title"] == "A wide shot of the harbour"
    assert item["source"]["label"] == "Harbour Board · Canvas"
    assert item["source"]["deep_link"] == f"/team/{SCOPE}/canvas/{CANVAS}?node=node-7"
    # Two rows, one canvas lookup — not one query per card.
    assert len(canvases.calls) == 1


async def test_list_passes_filters_through_to_the_repository():
    svc, repo, _p = build()
    since = datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc)
    await svc.list(
        SCOPE,
        str(SCOPE),
        state="saved",
        origin_kinds=["canvas_run"],
        project_id=42,
        canvas_id=900000000000000001,
        media_kind="image",
        model="doubao-seedream",
        since=since,
        source_asset_id=727145299382534300,
        cursor="c1",
        limit=7,
    )
    assert repo.last_filters == {
        "state": "saved",
        "origin_kinds": ["canvas_run"],
        "project_id": 42,
        "canvas_id": 900000000000000001,
        "media_kind": "image",
        "model": "doubao-seedream",
        "since": since,
        "source_asset_id": 727145299382534300,
        "include_intermediate": False,
        "cursor": "c1",
        "limit": 7,
    }


async def test_empty_origin_kind_is_labelled_unknown_not_blank():
    svc, _repo, _p = build(rows=[make_row(origin_kind="", canvas_id=None)])
    item = (await svc.list(SCOPE, str(SCOPE)))["items"][0]
    assert item["origin_kind"] == "unknown"
    assert item["source"]["kind"] == "unknown"
    assert item["source"]["label"] == "unknown"


async def test_none_origin_kind_is_labelled_unknown():
    svc, _repo, _p = build(rows=[make_row(origin_kind=None, canvas_id=None)])
    item = (await svc.list(SCOPE, str(SCOPE)))["items"][0]
    assert item["source"]["label"] == "unknown"


async def test_counts_drops_the_deleted_bucket():
    svc, _repo, _p = build()
    assert await svc.counts(SCOPE) == {"unreviewed": 4, "saved": 2, "in_assets": 1}


# ── save ───────────────────────────────────────────────────────────────────


async def test_save_promotes_and_returns_the_refreshed_item():
    svc, _repo, promote = build()
    out = await svc.save(GEN, SCOPE, USER)
    assert promote.calls == [
        {"gen_id": int(GEN), "user_id": USER, "target_scope_id": SCOPE}
    ]
    assert out["review_state"] == "saved"
    assert out["promoted_resource_id"] == RESOURCE
    assert out["title"] and out["source"]["label"]


async def test_save_maps_permission_error_to_403():
    svc, _repo, _p = build(promote=FakePromote(PermissionError("nope")))
    with pytest.raises(AssetError) as ei:
        await svc.save(GEN, SCOPE, USER)
    assert (ei.value.status, ei.value.code) == (403, "not_authorised")
    assert ei.value.detail == "nope"


async def test_save_maps_not_found_to_404():
    svc, _repo, _p = build(promote=FakePromote(ValueError("generation not found")))
    with pytest.raises(AssetError) as ei:
        await svc.save(GEN, SCOPE, USER)
    assert (ei.value.status, ei.value.code) == (404, "generation_not_found")


async def test_save_maps_file_missing_to_409():
    svc, _repo, _p = build(promote=FakePromote(ValueError("generation file missing")))
    with pytest.raises(AssetError) as ei:
        await svc.save(GEN, SCOPE, USER)
    assert (ei.value.status, ei.value.code) == (409, "file_missing")


async def test_save_reraises_an_unmapped_value_error():
    svc, _repo, _p = build(promote=FakePromote(ValueError("storage backend refused")))
    with pytest.raises(ValueError, match="storage backend refused"):
        await svc.save(GEN, SCOPE, USER)


async def test_save_404s_when_the_row_is_not_in_this_scope():
    svc, repo, _p = build()
    repo.rows.clear()  # promoted, but the row is not readable in this scope
    with pytest.raises(AssetError) as ei:
        await svc.save(GEN, SCOPE, USER)
    assert ei.value.status == 404


# ── save_as_asset ──────────────────────────────────────────────────────────


async def test_save_as_asset_attaches_promoted_resource_and_sets_in_assets(fake_uow):
    assets = FakeAssets()
    svc, repo, _p = build(assets=assets, log=fake_uow.log)
    out = await svc.save_as_asset(
        GEN,
        SCOPE,
        USER,
        SaveAsAssetRequest(asset_id=ASSET, slot="stills"),
    )
    assert assets.created == []
    (aid, sid, req, uid) = assets.attached[0]
    assert (aid, sid, uid) == (int(ASSET), SCOPE, USER)
    assert (req.resource_id, req.slot, req.loadout_id) == (RESOURCE, "stills", None)
    assert repo.state_writes == [(int(GEN), SCOPE, "in_assets")]
    assert out["asset_id"] == ASSET
    assert out["resource_id"] == RESOURCE
    assert out["generation"]["review_state"] == "in_assets"
    assert fake_uow.enters == 1
    # promote runs BEFORE the transaction opens: its `SET LOCAL ROLE
    # service_role` (canvas-origin rows) and its storage I/O must not ride
    # inside the transaction that then writes the asset rows.
    # The destination is validated BEFORE promote: a 404/422 about the asset
    # or the slot must not leave a promoted resource in My Uploads behind it.
    assert fake_uow.log == [
        "require_writable",
        "promote",
        "uow_enter",
        "attach_file",
        "set_review_state:in_assets",
        "uow_exit",
    ]


async def test_save_as_asset_creates_the_asset_first_when_asked(fake_uow):
    assets = FakeAssets()
    svc, _repo, _p = build(assets=assets, log=fake_uow.log)
    out = await svc.save_as_asset(
        GEN,
        SCOPE,
        USER,
        SaveAsAssetRequest(
            new_asset=NewAssetSpec(asset_type="character", name="Harbour Girl"),
            slot="stills",
        ),
    )
    (scope, payload, uid) = assets.created[0]
    assert (scope, payload.name, payload.asset_type, uid) == (
        SCOPE,
        "Harbour Girl",
        "character",
        USER,
    )
    assert assets.attached[0][0] == int(ASSET)
    assert out["asset_id"] == ASSET
    assert fake_uow.log == [
        "promote",
        "uow_enter",
        "create_asset",
        "attach_file",
        "set_review_state:in_assets",
        "uow_exit",
    ]


async def test_save_as_asset_leaves_state_untouched_when_attach_fails(fake_uow):
    """The accepted weaker guarantee, for a failure only the ATTACH can see.

    The raise is ``resource_not_found`` on purpose: an invalid slot no longer
    reaches this far (``_validate_target`` refuses it before ``promote``), so
    using one here would silently stop testing the attach-failure path.
    """
    assets = FakeAssets(
        attach_raises=AssetError(404, "resource_not_found", "Resource not found")
    )
    svc, repo, _p = build(assets=assets, log=fake_uow.log)
    with pytest.raises(AssetError) as ei:
        await svc.save_as_asset(
            GEN, SCOPE, USER, SaveAsAssetRequest(asset_id=ASSET, slot="stills")
        )
    assert ei.value.code == "resource_not_found"
    assert repo.state_writes == []  # rollback is the UoW's job; we never wrote
    # The accepted weaker guarantee: promote is already committed, so the row
    # sits at `saved` — a legitimate state. What must never happen is
    # `in_assets` with nothing attached.
    assert repo.rows[int(GEN)]["review_state"] == "saved"
    assert fake_uow.enters == 1 and fake_uow.exits == 1
    assert fake_uow.log == [
        "require_writable",
        "promote",
        "uow_enter",
        "attach_file",
        "uow_rollback",
    ]


async def test_save_as_asset_propagates_a_create_conflict(fake_uow):
    assets = FakeAssets(
        create_raises=AssetError(409, "asset_exists", "already exists", {"a": 1})
    )
    svc, repo, _p = build(assets=assets)
    with pytest.raises(AssetError) as ei:
        await svc.save_as_asset(
            GEN,
            SCOPE,
            USER,
            SaveAsAssetRequest(
                new_asset=NewAssetSpec(asset_type="prop", name="Lantern")
            ),
        )
    assert (ei.value.status, ei.value.code, ei.value.extra) == (
        409,
        "asset_exists",
        {"a": 1},
    )
    assert assets.attached == [] and repo.state_writes == []


# ── I1: a refusal must not have half-applied ───────────────────────────────
#
# ``promote`` copies bytes and creates a resource. Every failure that is
# knowable BEFORE it runs must therefore run before it — otherwise the caller
# gets a 404/422 that already put a file in their library.


async def test_save_refuses_an_out_of_scope_generation_without_promoting(fake_uow):
    """404 for a generation in another scope, with nothing copied.

    ``promote`` resolves a generation by id alone (it carries its own
    source-scope grant), so a gen_id in a DIFFERENT scope the caller belongs
    to would promote successfully and only then fail the scope re-read.
    """
    svc, _repo, promote = build(
        rows=[make_row(scope_id=str(SCOPE + 1))], log=fake_uow.log
    )
    with pytest.raises(AssetError) as ei:
        await svc.save(GEN, SCOPE, USER)
    assert (ei.value.status, ei.value.code) == (404, "generation_not_found")
    assert promote.calls == []
    assert fake_uow.log == []


async def test_save_as_asset_refuses_an_unknown_asset_without_promoting(fake_uow):
    assets = FakeAssets(
        require_raises=AssetError(404, "asset_not_found", "Asset not found")
    )
    svc, repo, promote = build(assets=assets, log=fake_uow.log)
    with pytest.raises(AssetError) as ei:
        await svc.save_as_asset(
            GEN, SCOPE, USER, SaveAsAssetRequest(asset_id=ASSET, slot="stills")
        )
    assert (ei.value.status, ei.value.code) == (404, "asset_not_found")
    assert promote.calls == []
    assert repo.state_writes == [] and fake_uow.enters == 0


async def test_save_as_asset_refuses_a_system_preset_without_promoting(fake_uow):
    assets = FakeAssets(
        require_raises=AssetError(403, "system_preset_readonly", "read-only")
    )
    svc, _repo, promote = build(assets=assets, log=fake_uow.log)
    with pytest.raises(AssetError) as ei:
        await svc.save_as_asset(
            GEN, SCOPE, USER, SaveAsAssetRequest(asset_id=ASSET, slot="stills")
        )
    assert (ei.value.status, ei.value.code) == (403, "system_preset_readonly")
    assert promote.calls == []


async def test_save_as_asset_refuses_an_invalid_slot_without_promoting(fake_uow):
    """The slot is checked against the EXISTING asset's type, read first."""
    assets = FakeAssets()  # _require_writable answers asset_type='character'
    svc, _repo, promote = build(assets=assets, log=fake_uow.log)
    with pytest.raises(AssetError) as ei:
        await svc.save_as_asset(
            GEN, SCOPE, USER, SaveAsAssetRequest(asset_id=ASSET, slot="turnaround")
        )
    assert (ei.value.status, ei.value.code) == (422, "invalid_slot")
    assert promote.calls == []
    assert assets.attached == [] and fake_uow.enters == 0


async def test_save_as_asset_refuses_an_invalid_slot_for_a_new_asset(fake_uow):
    """new_asset has no row to read — the slot is checked against its type."""
    svc, _repo, promote = build(log=fake_uow.log)
    with pytest.raises(AssetError) as ei:
        await svc.save_as_asset(
            GEN,
            SCOPE,
            USER,
            SaveAsAssetRequest(
                new_asset=NewAssetSpec(asset_type="prop", name="Lantern"),
                slot="stills",  # a character slot, not a prop slot
            ),
        )
    assert (ei.value.status, ei.value.code) == (422, "invalid_slot")
    assert promote.calls == []
    assert fake_uow.enters == 0


async def test_save_as_asset_maps_promote_permission_error(fake_uow):
    svc, _repo, _p = build(promote=FakePromote(PermissionError("no")), log=fake_uow.log)
    with pytest.raises(AssetError) as ei:
        await svc.save_as_asset(GEN, SCOPE, USER, SaveAsAssetRequest(asset_id=ASSET))
    assert ei.value.status == 403
    # promote is outside the transaction, so a rejected promote never opens one.
    assert fake_uow.enters == 0


# ── delete ─────────────────────────────────────────────────────────────────


async def test_delete_removes_the_row():
    svc, repo, _p = build()
    assert await svc.delete(GEN, SCOPE) is None
    assert repo.deleted == [int(GEN)]


async def test_delete_404s_when_nothing_matched():
    svc, repo, _p = build()
    repo.delete_returns[int(GEN)] = False
    with pytest.raises(AssetError) as ei:
        await svc.delete(GEN, SCOPE)
    assert (ei.value.status, ei.value.code) == (404, "generation_not_found")


# ── batch ──────────────────────────────────────────────────────────────────


async def test_batch_attempts_every_id_and_reports_failures_with_codes():
    good, bad = GEN, "800000000000000099"
    svc, repo, _p = build(rows=[make_row(id=good)])
    repo.delete_returns[int(bad)] = False
    out = await svc.batch(BatchRequest(ids=[bad, good], action="delete"), SCOPE, USER)
    # The failing id came FIRST — a batch does not stop at the first failure.
    assert out["ok"] == [good]
    assert out["failed"] == [
        {"id": bad, "code": "generation_not_found", "detail": "Generation not found"}
    ]
    assert repo.deleted == [int(bad), int(good)]


async def test_batch_save_reports_per_item_permission_failures():
    svc, _repo, _p = build(promote=FakePromote(PermissionError("nope")))
    out = await svc.batch(BatchRequest(ids=[GEN], action="save"), SCOPE, USER)
    assert out["ok"] == []
    assert out["failed"][0]["code"] == "not_authorised"


async def test_batch_save_as_asset_uses_the_request_payload(fake_uow):
    assets = FakeAssets()
    svc, _repo, _p = build(assets=assets, log=fake_uow.log)
    out = await svc.batch(
        BatchRequest(
            ids=[GEN],
            action="save_as_asset",
            save_as_asset=SaveAsAssetRequest(asset_id=ASSET, slot="stills"),
        ),
        SCOPE,
        USER,
    )
    assert out["ok"] == [GEN]
    assert assets.attached[0][2].slot == "stills"
    assert fake_uow.enters == 1


async def test_batch_builds_no_cards_for_the_ids_it_only_reports(fake_uow):
    """A batch reports ids, so it must not pay for a card per item.

    Each decoration is a ``names_by_ids`` round-trip; at the schema's 200-id
    ceiling that is 200 queries whose only output is discarded.
    """
    canvases = FakeCanvases()
    rows = [make_row(id=str(800000000000000000 + i)) for i in range(5)]
    svc, _repo, _p = build(rows=rows, canvases=canvases, log=fake_uow.log)
    out = await svc.batch(
        BatchRequest(ids=[r["id"] for r in rows], action="save"), SCOPE, USER
    )
    assert out["ok"] == [r["id"] for r in rows]
    assert canvases.calls == []


async def test_batch_save_as_asset_builds_no_cards_either(fake_uow):
    canvases = FakeCanvases()
    rows = [make_row(id=str(800000000000000000 + i)) for i in range(3)]
    svc, _repo, _p = build(rows=rows, canvases=canvases, log=fake_uow.log)
    await svc.batch(
        BatchRequest(
            ids=[r["id"] for r in rows],
            action="save_as_asset",
            save_as_asset=SaveAsAssetRequest(asset_id=ASSET),
        ),
        SCOPE,
        USER,
    )
    assert canvases.calls == []
    assert fake_uow.enters == 3  # one transaction per item, not one for all


# ── cleanup ────────────────────────────────────────────────────────────────


def _old_rows(n):
    return [
        make_row(
            id=str(800000000000000000 + i),
            created_at=NOW - datetime.timedelta(days=200),
        )
        for i in range(n)
    ]


async def test_cleanup_dry_run_deletes_nothing_and_samples_twelve():
    svc, repo, _p = build(rows=_old_rows(20))
    out = await svc.cleanup(CleanupRequest(older_than_days=30), SCOPE)
    assert out["dry_run"] is True
    assert out["count"] == 20
    assert out["deleted"] == 0
    assert len(out["sample"]) == 12
    assert out["sample"][0]["title"]
    assert repo.deleted == []
    scope, older_than, limit = repo.cleanup_call
    assert scope == SCOPE and limit == CLEANUP_SCAN_LIMIT
    # 30 days back from "now", not from the row timestamps.
    delta = datetime.datetime.now(datetime.timezone.utc) - older_than
    assert datetime.timedelta(days=29) < delta < datetime.timedelta(days=31)


async def test_cleanup_live_deletes_and_counts():
    svc, repo, _p = build(rows=_old_rows(3))
    out = await svc.cleanup(CleanupRequest(older_than_days=30, dry_run=False), SCOPE)
    assert out == {
        "dry_run": False,
        "count": 3,
        "sample": [],
        "deleted": 3,
        "truncated": False,
    }
    assert len(repo.deleted) == 3


async def test_cleanup_live_counts_only_the_rows_it_really_deleted():
    rows = _old_rows(3)
    svc, repo, _p = build(rows=rows)
    repo.delete_returns[int(rows[1]["id"])] = False
    out = await svc.cleanup(CleanupRequest(older_than_days=30, dry_run=False), SCOPE)
    assert (out["count"], out["deleted"]) == (3, 2)


@pytest.mark.parametrize("dry_run", [True, False])
async def test_cleanup_flags_a_pass_that_hit_the_scan_cap(monkeypatch, dry_run):
    """A capped pass reports ``count`` for the window it saw, not for the scope.

    Without ``truncated`` the two are indistinguishable on the wire, and a
    caller that deletes ``count`` rows and stops would leave the rest behind
    while reporting the cleanup as complete.
    """
    monkeypatch.setattr(mod, "CLEANUP_SCAN_LIMIT", 3)
    svc, _repo, _p = build(rows=_old_rows(3))
    out = await svc.cleanup(CleanupRequest(older_than_days=30, dry_run=dry_run), SCOPE)
    assert out["count"] == 3 and out["truncated"] is True


async def test_cleanup_does_not_flag_a_pass_that_fits():
    svc, _repo, _p = build(rows=_old_rows(3))
    out = await svc.cleanup(CleanupRequest(older_than_days=30), SCOPE)
    assert out["truncated"] is False


def test_the_scan_cap_and_the_truncated_flag_are_one_constant():
    """The number the service ASKS for and the number the SQL will give are
    the same object, not two copies that agree today.

    If the request grew past the repo's cap, every pass would come back short
    of what ``truncated`` compares against — the flag would read ``False``
    forever and a partial cleanup would report itself complete. Nothing else
    in the suite can see that: the repo is a fake everywhere above, so it
    happily returns whatever ``limit`` asks for.
    """
    import inspect

    from app.repositories import generated_media_repository as repo_mod

    assert mod.CLEANUP_SCAN_LIMIT is repo_mod.CLEANUP_SCAN_LIMIT
    # The SQL cap is expressed in terms of the constant, not a literal — a
    # re-hardcoded ``2000`` here is the drift this test exists to catch.
    src = inspect.getsource(repo_mod.GeneratedMediaRepository.list_older_unreviewed)
    assert "CLEANUP_SCAN_LIMIT" in src
    cleanup_src = inspect.getsource(mod.GeneratedInboxService.cleanup)
    assert cleanup_src.count("CLEANUP_SCAN_LIMIT") >= 2  # the call and the flag


async def test_cleanup_skips_rows_that_are_already_reviewed():
    svc, _repo, _p = build(
        rows=[
            make_row(
                id="800000000000000010",
                review_state="saved",
                created_at=NOW - datetime.timedelta(days=200),
            )
        ]
    )
    out = await svc.cleanup(CleanupRequest(older_than_days=30), SCOPE)
    assert out["count"] == 0


# ── contract ───────────────────────────────────────────────────────────────


async def test_a_wire_shape_row_round_trips_through_generated_item():
    """A repo row as it reaches JSON (string ids, ISO created_at) validates.

    The row shape is the boundary this service is most likely to break on —
    ``GeneratedItem.model_validate`` has to accept exactly what
    ``_normalize`` produces, extra repo-only columns and all.
    """
    row = make_row(created_at="2026-08-20T12:00:00+00:00")
    svc, _repo, _p = build(rows=[row])
    item = (await svc.list(SCOPE, str(SCOPE)))["items"][0]
    assert item["id"] == GEN and isinstance(item["id"], str)
    assert item["created_at"] == NOW
    assert item["review_state"] == "unreviewed"
    # repo-only columns never reach the wire model
    assert "file_path" not in item and "params" not in item
    assert set(item["source"]) == {
        "kind",
        "label",
        "canvas_id",
        "node_id",
        "shot_id",
        "conversation_id",
        "deep_link",
        # 3a run provenance — always present as keys, filled only for an
        # agent_run row that is actually in the deliverable registry.
        "issue_id",
        "run_id",
        "step",
    }


async def test_names_by_ids_short_circuits_on_an_empty_page():
    """The service calls ``names_by_ids`` unconditionally — including for a page
    with no canvas rows at all — so the real repo has to answer an empty id list
    without a round-trip. (No DB engine is configured here: if it issued the
    query, this test would error rather than pass.)"""
    from app.repositories.canvas_repository import CanvasRepository

    assert await CanvasRepository().names_by_ids([]) == {}


async def test_a_page_with_no_canvas_rows_still_makes_exactly_one_lookup():
    canvases = FakeCanvases()
    svc, _repo, _p = build(rows=[make_row(origin_kind="agent_run", canvas_id=None)])
    svc.canvases = canvases
    item = (await svc.list(SCOPE, str(SCOPE)))["items"][0]
    assert item["source"]["label"] == "Chat generation"
    assert canvases.calls == [[]]


async def test_list_hides_intermediates_unless_asked():
    """The inbox default and the opt-in, both reaching the repository.

    The default matters more than it looks: ``include_intermediate`` absent
    from the call would let the repository's own default decide, and this
    service is where "the inbox does not show masks" is a stated contract
    rather than an accident of a downstream signature.
    """
    svc, repo, _p = build()

    await svc.list(SCOPE, str(SCOPE))
    assert repo.last_filters["include_intermediate"] is False

    await svc.list(SCOPE, str(SCOPE), include_intermediate=True)
    assert repo.last_filters["include_intermediate"] is True


# ── one item by id (P4 T7) ─────────────────────────────────────────────────


async def test_get_item_is_the_same_card_the_list_ships():
    """One row, two reads, one shape.

    Not "both look plausible" — the SAME row through both methods, compared
    whole. A by-id read that built its own body would be a second wire shape
    for one row, and the client would have to know which endpoint a card came
    from before it could render it.
    """
    row = make_row()
    svc, _repo, _p = build(rows=[row])

    listed = (await svc.list(SCOPE, str(SCOPE)))["items"][0]
    one = await svc.get_item(GEN, SCOPE)

    assert one == listed
    # And it really is the decorated card, not the raw repo row: the two
    # derived fields are present and the private columns are gone.
    assert one["source"]["label"] == "Harbour Board · Canvas"
    assert one["title"] == "A wide shot of the harbour"
    assert "file_path" not in one and "params" not in one and "cost_cents" not in one


async def test_get_item_resolves_the_canvas_name_like_the_list_does():
    canvases = FakeCanvases()
    svc, _repo, _p = build(rows=[make_row()], canvases=canvases)

    await svc.get_item(GEN, SCOPE)

    assert canvases.calls == [[int(CANVAS)]]


async def test_get_item_refuses_a_row_in_another_scope():
    """Same refusal as ``save``/``delete``: an id that exists but is not in
    this scope must not be readable, and "not there" and "not yours" must be
    the same answer to a caller who may not learn which."""
    svc, _repo, _p = build(rows=[make_row(scope_id=str(SCOPE + 1))])

    with pytest.raises(AssetError) as e:
        await svc.get_item(GEN, SCOPE)
    assert (e.value.status, e.value.code) == (404, "generation_not_found")


async def test_get_item_404s_on_an_unknown_id():
    svc, _repo, _p = build(rows=[make_row()])

    with pytest.raises(AssetError) as e:
        await svc.get_item("800000000000000009", SCOPE)
    assert e.value.status == 404


async def test_get_item_reads_a_deleted_row_because_the_actions_on_it_do():
    """``review_state='deleted'`` hides a row from the LIST, not from the row
    reads: ``save`` and ``delete`` both resolve it through the same
    ``gen_repo.get``. A stricter by-id read would mean "you can promote it but
    you cannot look at it"."""
    svc, _repo, _p = build(rows=[make_row(review_state="deleted")])

    assert (await svc.get_item(GEN, SCOPE))["review_state"] == "deleted"
