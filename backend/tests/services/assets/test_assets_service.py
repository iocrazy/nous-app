"""AssetsService invariants with in-memory fake repos (no DB)."""

from __future__ import annotations

import datetime

import pytest

from app.repositories.assets_repository import DuplicateAssetName
from app.schemas.assets import (
    AssetCreate,
    AssetUpdate,
    AttachFileRequest,
    LinkRequest,
    LoadoutCreate,
    LoadoutUpdate,
)
from app.services.assets.assets_service import AssetError, AssetsService

NOW = datetime.datetime(2026, 8, 28, tzinfo=datetime.timezone.utc)
SCOPE = 727145299382534200
USER = "11111111-1111-1111-1111-111111111111"
OTHER_OWNER = "22222222-2222-2222-2222-222222222222"


class FakeAssetsRepo:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.list_calls: list[dict] = []
        self._next = 1000

    def _row(self, **kw):
        base = {
            "subtype": None,
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
            # mig 449 — the real column is NOT NULL DEFAULT true, so a row that
            # reaches this fake without the key must look like one the database
            # produced, not like one missing a field.
            "in_library": True,
            "tags": {},
            "sort_order": 0,
            "created_by": USER,
            "created_at": NOW,
            "updated_at": NOW,
            "deleted_at": None,
        }
        base.update(kw)
        return base

    async def create(self, scope_id, fields, created_by):
        for r in self.rows.values():
            if (
                r["asset_type"] == fields["asset_type"]
                and r["name"].lower() == fields["name"].lower()
            ):
                raise DuplicateAssetName(existing_id=r["id"])
        self._next += 1
        row = self._row(id=self._next, scope_id=scope_id, **fields)
        self.rows[self._next] = row
        return row

    async def find_by_name(self, scope_id, asset_type, name):
        for r in self.rows.values():
            if (
                r["scope_id"] == scope_id
                and r["asset_type"] == asset_type
                and r["name"].lower() == name.lower()
            ):
                return r
        return None

    async def create_raw(self, fields):
        """Mirrors the real INSERT taking the FULL column dict (scope_id /
        source / duplicated_from / is_system_preset are the caller's, not a
        schema default) — and, like it, raises WITHOUT a follow-up lookup for
        the existing id: that query would run on an already-aborted
        transaction. The uniqueness key is the real index's,
        ``COALESCE(scope_id,0) + asset_type + lower(name)``.
        """
        key = (
            fields.get("scope_id") or 0,
            fields["asset_type"],
            fields["name"].lower(),
        )
        for r in self.rows.values():
            if (r["scope_id"] or 0, r["asset_type"], r["name"].lower()) == key:
                raise DuplicateAssetName(existing_id=0)
        self._next += 1
        row = self._row(id=self._next, **fields)
        self.rows[self._next] = row
        return row

    @staticmethod
    def _visible(row, scope_id):
        """Mirrors the real predicate: or_(scope_id == X, is_system_preset)."""
        return row["scope_id"] == scope_id or row["is_system_preset"]

    async def get(self, asset_id, scope_id):
        r = self.rows.get(int(asset_id))
        return r if r and self._visible(r, scope_id) else None

    async def resolve_legacy(self, scope_id, legacy_table, legacy_id):
        """In-memory stand-in for the JSONB containment read.

        Models the same three predicates the real statement carries — own scope
        (presets are NOT unioned in, unlike ``get``), not soft-deleted, and the
        ``[table, id]`` pair present in ``attrs.legacy_ids`` — plus the ``ORDER
        BY id`` tiebreak, so a test can tell the two apart.
        """
        want = [str(legacy_table), int(legacy_id)]
        hits = [
            r
            for r in self.rows.values()
            if r.get("scope_id") is not None
            and int(r["scope_id"]) == int(scope_id)
            and r.get("deleted_at") is None
            and any(
                list(pair) == want
                for pair in (r.get("attrs") or {}).get("legacy_ids", [])
            )
        ]
        return min((int(r["id"]) for r in hits), default=None)

    async def list(self, scope_id, **kw):
        self.list_calls.append(kw)
        return [r for r in self.rows.values() if self._visible(r, scope_id)]

    async def update(self, asset_id, scope_id, fields):
        # The real UPDATE carries a scope predicate and returns None when it
        # matches nothing (presets have scope_id NULL, so they never match) —
        # and stamps ``updated_at`` in the same statement (``assets`` ships no
        # touch trigger, mig 445), which is the only reason a header write
        # moves the "recent" shelf. Mirrored here so a caller that forgets to
        # go through the repo cannot look like it bumped the clock.
        r = self.rows.get(int(asset_id))
        if r is None or r["scope_id"] != scope_id:
            return None
        r.update(fields)
        r["updated_at"] = datetime.datetime.now(datetime.timezone.utc)
        return r

    async def soft_delete(self, asset_id, scope_id):
        r = self.rows.get(int(asset_id))
        if r is None or r["scope_id"] != scope_id:
            return False
        del self.rows[int(asset_id)]
        return True

    def make_preset(self, asset_id):
        """A system preset carries is_system_preset AND scope_id NULL — the
        assets_scope_or_preset CHECK forbids any other combination — and
        ``source="system_preset"``.

        The source is stamped HERE rather than passed through ``AssetCreate``:
        the seeder writes the preset row directly, and ``AssetCreate.source``
        is now the client-facing allowlist (``manual`` / ``generated``), which
        deliberately cannot express this one. Setting all three together is
        also the only combination the real row ever has, so a fixture cannot
        drift into a half-preset the CHECK would have rejected.
        """
        row = self.rows[int(asset_id)]
        row["is_system_preset"] = True
        row["scope_id"] = None
        row["source"] = "system_preset"
        return row

    async def slot_counts(self, ids):
        return {}

    async def project_ids(self, ids):
        return {}

    async def loadout_counts(self, ids):
        return {}


class FakeRelationsRepo:
    def __init__(self):
        self.files, self.links, self.loadouts, self.refs = [], [], {}, []
        self.strip_calls = []
        self.touched: list[int] = []
        self.scope_checks: list[tuple[int, int]] = []
        self._next = 5000
        self.in_scope_resources = {727145299382534146}
        # Resources whose ``resources`` ROW still exists but which have left
        # the caller's scope (``delete_resource_item`` drops the
        # ``resource_items`` row and the trigger trashes the resource; the
        # ``asset_files`` row survives, its FK being on ``resources.id``).
        #
        # A separate set because the two real methods disagree ON PURPOSE and
        # the fake has to disagree the same way: ``resource_in_scope`` joins
        # ``resource_items`` and answers False, while ``resource_media_rows``
        # selects on ``Resources.id.in_(...)`` with NO scope predicate at all
        # and still returns the row. Folding both onto ``in_scope_resources``
        # would make a "descoped reference is skipped" test pass whether or not
        # the service re-checks — the fake would be doing the guard's job.
        self.descoped_resources: set[int] = set()
        # resource_id -> the media columns the reference-materialization step
        # reads. Column names/shape mirror the REAL projection
        # (Resources.file_path / mime_type / thumbnail_path /
        # cover_image_path); anything attached but not listed here defaults to
        # a plain image original, which is what an attached file usually is.
        self.resource_media: dict[int, dict] = {}

    async def resource_media_rows(self, resource_ids, *, system_reason):
        """Mirrors the real repo: a MISSING id is simply absent from the dict
        (that is how the caller learns "resource_not_found"), never a row of
        Nones.

        ⚠️ What this fake CANNOT mirror is the real method's
        ``system_request_scope`` wrap — ``Resources`` is the one scope-enforced
        table the assets router touches, and a dict lookup passes no choke
        point. That wiring is pinned by
        ``tests/test_assets_reference_scope_wiring.py``; do not read a green
        run here as evidence the production read is scoped. ``system_reason``
        IS mirrored as required keyword-only, so a caller that forgets to name
        its audit reason fails here too.
        """
        assert system_reason, "the cross-user read must carry an audit reason"
        out = {}
        for raw in resource_ids:
            rid = int(raw)
            if rid in self.resource_media:
                out[rid] = {"id": rid, **self.resource_media[rid]}
            elif rid in self.in_scope_resources or rid in self.descoped_resources:
                out[rid] = {
                    "id": rid,
                    "file_path": f"library/{rid}/original.png",
                    "mime_type": "image/png",
                    "thumbnail_path": None,
                    "cover_image_path": None,
                }
        return out

    async def resource_in_scope(self, resource_id, scope_id):
        self.scope_checks.append((int(resource_id), int(scope_id)))
        return int(resource_id) in self.in_scope_resources

    async def touch_asset(self, asset_id):
        """``assets`` ships no touch trigger (mig 445), so every relation write
        bumps ``updated_at`` itself. Recorded here so the tests can assert the
        bump happened per write instead of trusting the call site."""
        self.touched.append(int(asset_id))
        return True

    async def attach(self, asset_id, resource_id, slot, **kw):
        row = {
            "asset_id": asset_id,
            "resource_id": resource_id,
            "slot": slot,
            "loadout_id": kw.get("loadout_id"),
            "sort_order": kw.get("sort_order", 0),
            "note": kw.get("note"),
            "attached_by": kw.get("attached_by"),
            "attached_at": NOW,
        }
        self.files.append(row)
        return row

    async def detach(self, asset_id, resource_id, slot):
        before = len(self.files)
        self.files = [
            f
            for f in self.files
            if not (
                f["asset_id"] == asset_id
                and f["resource_id"] == resource_id
                and f["slot"] == slot
            )
        ]
        return len(self.files) < before

    async def list_files(self, asset_id):
        return [f for f in self.files if f["asset_id"] == asset_id]

    async def add_link(self, f, t, rel):
        row = {"from_asset_id": f, "to_asset_id": t, "relation": rel, "created_at": NOW}
        self.links.append(row)
        return row

    async def remove_link(self, f, t, rel):
        before = len(self.links)
        self.links = [
            x
            for x in self.links
            if not (
                x["from_asset_id"] == f
                and x["to_asset_id"] == t
                and x["relation"] == rel
            )
        ]
        return len(self.links) < before

    async def list_links(self, asset_id):
        return (
            [x for x in self.links if x["from_asset_id"] == asset_id],
            [x for x in self.links if x["to_asset_id"] == asset_id],
        )

    async def link_targets(self, f, rel):
        return {
            x["to_asset_id"]
            for x in self.links
            if x["from_asset_id"] == f and x["relation"] == rel
        }

    async def create_loadout(self, asset_id, fields):
        self._next += 1
        row = {
            "id": self._next,
            "asset_id": asset_id,
            "is_default": False,
            "costume_ids": [],
            "prop_ids": [],
            "prompt_extra": None,
            "sort_order": 0,
            "created_at": NOW,
            **fields,
        }
        self.loadouts[self._next] = row
        return row

    async def update_loadout(self, lid, asset_id, fields):
        # The real UPDATE has .where(asset_id == ...) and returns None when the
        # loadout is unknown or belongs to another asset; the empty-fields path
        # looks it up through list_loadouts(asset_id) and is equally filtered.
        row = self.loadouts.get(lid)
        if row is None or row["asset_id"] != asset_id:
            return None
        row.update(fields)
        return row

    async def delete_loadout(self, lid, asset_id):
        row = self.loadouts.get(lid)
        if not row or row["is_default"]:
            return False
        del self.loadouts[lid]
        return True

    async def list_loadouts(self, asset_id):
        return [lo for lo in self.loadouts.values() if lo["asset_id"] == asset_id]

    async def set_default(self, lid, asset_id):
        """Mirrors the repo contract: False when the loadout is not owned."""
        target = self.loadouts.get(lid)
        if target is None or target["asset_id"] != asset_id:
            return False
        for lo in self.loadouts.values():
            if lo["asset_id"] == asset_id:
                lo["is_default"] = lo["id"] == lid
        return True

    async def strip_from_loadouts(self, asset_id, costume_id=None, prop_id=None):
        self.strip_calls.append((asset_id, costume_id, prop_id))
        return 0

    # project_id -> (team_id, owner_id). team_id None = personal project, whose
    # asset scope is the OWNER's personal team (resolved by the service).
    project_teams = {
        55: (SCOPE, USER),
        56: (999, USER),
        58: (None, OTHER_OWNER),
        59: (None, None),
    }

    async def project_team_id(self, project_id):
        if project_id not in self.project_teams:
            return False, None, None
        team_id, owner_id = self.project_teams[project_id]
        return True, team_id, owner_id

    async def link_project(self, a, p, u):
        """Mirrors the real INSERT ... ON CONFLICT DO NOTHING: True only when a
        NEW row landed. A fake that always answered True would let
        ``import_from_script``'s ``linked`` vs ``already_linked`` distinction
        pass whether or not the service reads the repo's answer."""
        if (a, p) in self.refs:
            return False
        self.refs.append((a, p))
        return True

    async def unlink_project(self, a, p):
        before = len(self.refs)
        self.refs = [r for r in self.refs if r != (a, p)]
        return len(self.refs) < before

    async def list_project_ids(self, a):
        return [p for (x, p) in self.refs if x == a]


class FakeCanvasRefsRepo:
    """The canvas→asset mirror, from the asset side (READ only — it is
    maintained by CanvasService on canvas save, never by these routes).

    ``calls`` records the scope the service handed down: that argument is the
    whole safety story of ``used_in`` (a system preset is readable from every
    scope, so an unscoped read would name other teams' canvases), and a fake
    that ignored it would let the filter be dropped without a test noticing.
    """

    def __init__(self):
        self.rows: list[dict] = []
        self.calls: list[tuple[str, str | None]] = []

    async def list_canvases_for_asset(self, asset_id, scope_id=None):
        self.calls.append((str(asset_id), None if scope_id is None else str(scope_id)))
        return list(self.rows)


@pytest.fixture
def svc():
    return AssetsService(
        assets_repo=FakeAssetsRepo(),
        relations_repo=FakeRelationsRepo(),
        canvas_refs_repo=FakeCanvasRefsRepo(),
    )


@pytest.mark.asyncio
async def test_create_character_creates_default_loadout(svc):
    a = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="Sang Yao"), USER
    )
    assert a["id"].isdigit()  # serialized
    los = await svc.relations.list_loadouts(int(a["id"]))
    assert len(los) == 1 and los[0]["is_default"] and los[0]["name"] == "Default"


@pytest.mark.asyncio
async def test_create_prop_has_no_loadout(svc):
    a = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="prop", name="Blade"), USER
    )
    assert await svc.relations.list_loadouts(int(a["id"])) == []


@pytest.mark.asyncio
async def test_duplicate_name_is_409_with_existing_id(svc):
    a = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="location", name="Bamboo Grove"), USER
    )
    with pytest.raises(AssetError) as ei:
        await svc.create_asset(
            SCOPE, AssetCreate(asset_type="location", name="bamboo grove"), USER
        )
    assert ei.value.status == 409 and ei.value.code == "asset_exists"
    assert ei.value.extra["existing_asset_id"] == a["id"]


@pytest.mark.asyncio
async def test_attach_validates_slot_and_scope(svc):
    a = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="Yi Heng"), USER
    )
    with pytest.raises(AssetError) as ei:
        await svc.attach_file(
            int(a["id"]),
            SCOPE,
            AttachFileRequest(resource_id="727145299382534146", slot="flat"),
            USER,
        )
    assert ei.value.status == 422 and ei.value.code == "invalid_slot"
    with pytest.raises(AssetError) as ei:
        await svc.attach_file(
            int(a["id"]), SCOPE, AttachFileRequest(resource_id="1", slot="sheet"), USER
        )
    assert ei.value.status == 404 and ei.value.code == "resource_not_found"
    f = await svc.attach_file(
        int(a["id"]),
        SCOPE,
        AttachFileRequest(resource_id="727145299382534146", slot="sheet"),
        USER,
    )
    assert f["slot"] == "sheet" and f["resource_id"] == "727145299382534146"


@pytest.mark.asyncio
async def test_attach_loadout_must_belong_to_asset(svc):
    a = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="A"), USER
    )
    b = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="B"), USER
    )
    b_default = (await svc.relations.list_loadouts(int(b["id"])))[0]
    with pytest.raises(AssetError) as ei:
        await svc.attach_file(
            int(a["id"]),
            SCOPE,
            AttachFileRequest(
                resource_id="727145299382534146",
                slot="stills",
                loadout_id=str(b_default["id"]),
            ),
            USER,
        )
    assert ei.value.code == "loadout_mismatch"


@pytest.mark.asyncio
async def test_link_type_rules_enforced(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    robe = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="costume", name="Robe"), USER
    )
    blade = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="prop", name="Blade"), USER
    )
    link = await svc.add_link(
        int(c["id"]), SCOPE, LinkRequest(to_asset_id=robe["id"], relation="wears")
    )
    assert link["relation"] == "wears"
    with pytest.raises(AssetError) as ei:
        await svc.add_link(
            int(c["id"]), SCOPE, LinkRequest(to_asset_id=blade["id"], relation="wears")
        )
    assert ei.value.code == "link_not_allowed"
    with pytest.raises(AssetError) as ei:
        await svc.add_link(
            int(robe["id"]), SCOPE, LinkRequest(to_asset_id=c["id"], relation="wears")
        )
    assert ei.value.code == "link_not_allowed"


@pytest.mark.asyncio
async def test_loadout_must_be_subset_of_links_and_character_only(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    robe = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="costume", name="Robe"), USER
    )
    hood = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="costume", name="Hood"), USER
    )
    await svc.add_link(
        int(c["id"]), SCOPE, LinkRequest(to_asset_id=robe["id"], relation="wears")
    )
    lo = await svc.create_loadout(
        int(c["id"]), SCOPE, LoadoutCreate(name="Day", costume_ids=[robe["id"]])
    )
    assert lo["costume_ids"] == [robe["id"]] and lo["is_default"] is False
    with pytest.raises(AssetError) as ei:
        await svc.create_loadout(
            int(c["id"]), SCOPE, LoadoutCreate(name="Night", costume_ids=[hood["id"]])
        )
    assert ei.value.code == "loadout_not_subset"
    with pytest.raises(AssetError) as ei:
        await svc.create_loadout(int(robe["id"]), SCOPE, LoadoutCreate(name="x"))
    assert ei.value.code == "loadouts_character_only"


@pytest.mark.asyncio
async def test_cannot_delete_default_loadout(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    default = (await svc.relations.list_loadouts(int(c["id"])))[0]
    with pytest.raises(AssetError) as ei:
        await svc.delete_loadout(int(c["id"]), SCOPE, int(default["id"]))
    assert ei.value.code == "cannot_delete_default"


@pytest.mark.asyncio
async def test_update_loadout_on_foreign_loadout_404(svc):
    """A loadout owned by another asset is invisible to this asset's UPDATE: the
    repo's asset_id predicate matches nothing and returns None, so the 404 fires
    at the `if not lo` check — set_default is never reached. The foreign asset
    keeps its own default because nothing was written."""
    a = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="A"), USER
    )
    b = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="B"), USER
    )
    b_default = (await svc.relations.list_loadouts(int(b["id"])))[0]
    with pytest.raises(AssetError) as ei:
        await svc.update_loadout(
            int(a["id"]), SCOPE, int(b_default["id"]), LoadoutUpdate(is_default=True)
        )
    assert ei.value.status == 404 and ei.value.code == "loadout_not_found"
    assert (await svc.relations.list_loadouts(int(b["id"])))[0]["is_default"] is True


@pytest.mark.asyncio
async def test_update_loadout_404_when_set_default_reports_no_write(svc):
    """Defense in depth for the OTHER way set_default can report failure: the
    UPDATE above succeeded, so ownership looked fine, but set_default's own
    SELECT ... FOR UPDATE found nothing and wrote nothing (the row vanished in
    between). A False there must not be swallowed into a fake success."""
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    other = await svc.create_loadout(int(c["id"]), SCOPE, LoadoutCreate(name="Night"))

    async def wrote_nothing(loadout_id, asset_id):
        return False

    svc.relations.set_default = wrote_nothing
    with pytest.raises(AssetError) as ei:
        await svc.update_loadout(
            int(c["id"]), SCOPE, int(other["id"]), LoadoutUpdate(is_default=True)
        )
    assert ei.value.status == 404 and ei.value.code == "loadout_not_found"


@pytest.mark.asyncio
async def test_update_loadout_set_default_promotes_own_loadout(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    default = (await svc.relations.list_loadouts(int(c["id"])))[0]
    other = await svc.create_loadout(int(c["id"]), SCOPE, LoadoutCreate(name="Night"))
    out = await svc.update_loadout(
        int(c["id"]), SCOPE, int(other["id"]), LoadoutUpdate(is_default=True)
    )
    assert out["is_default"] is True
    los = {int(lo["id"]): lo for lo in await svc.relations.list_loadouts(int(c["id"]))}
    assert los[int(other["id"])]["is_default"] is True
    assert los[int(default["id"])]["is_default"] is False


@pytest.mark.asyncio
async def test_get_detail_composes_files_links_loadouts_and_readiness(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    d = await svc.get_asset(int(c["id"]), SCOPE)
    assert d["readiness"] == {"state": "draft", "missing": ["sheet"]}
    assert d["files"] == [] and d["links"] == [] and len(d["loadouts"]) == 1
    with pytest.raises(AssetError) as ei:
        await svc.get_asset(999, SCOPE)
    assert ei.value.status == 404


@pytest.mark.asyncio
async def test_link_project_rejects_other_team_and_unknown(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    await svc.link_project(int(c["id"]), SCOPE, 55, USER)
    assert (int(c["id"]), 55) in svc.relations.refs
    with pytest.raises(AssetError) as ei:
        await svc.link_project(int(c["id"]), SCOPE, 56, USER)
    assert ei.value.code == "project_scope_mismatch"
    with pytest.raises(AssetError) as ei:
        await svc.link_project(int(c["id"]), SCOPE, 57, USER)
    assert ei.value.status == 404 and ei.value.code == "project_not_found"


@pytest.mark.asyncio
async def test_system_preset_is_readonly(svc):
    p = await svc.create_asset(
        SCOPE,
        AssetCreate(asset_type="prompt", name="Grid"),
        USER,
    )
    svc.assets.make_preset(p["id"])
    from app.schemas.assets import AssetUpdate

    with pytest.raises(AssetError) as ei:
        await svc.update_asset(int(p["id"]), SCOPE, AssetUpdate(description="x"))
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"


@pytest.mark.asyncio
async def test_delete_asset_refuses_system_preset(svc):
    """soft_delete carries a scope predicate a preset (scope_id NULL) never
    matches — it would return False silently, so refuse before writing."""
    p = await svc.create_asset(
        SCOPE,
        AssetCreate(asset_type="prompt", name="Grid"),
        USER,
    )
    svc.assets.make_preset(p["id"])
    with pytest.raises(AssetError) as ei:
        await svc.delete_asset(int(p["id"]), SCOPE)
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"
    assert int(p["id"]) in svc.assets.rows


@pytest.mark.asyncio
async def test_delete_asset_soft_deletes_ordinary_asset(svc):
    a = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="prop", name="Blade"), USER
    )
    await svc.delete_asset(int(a["id"]), SCOPE)
    assert int(a["id"]) not in svc.assets.rows
    with pytest.raises(AssetError) as ei:
        await svc.delete_asset(int(a["id"]), SCOPE)
    assert ei.value.status == 404 and ei.value.code == "asset_not_found"


@pytest.mark.asyncio
async def test_update_asset_404_when_row_vanishes_before_update(svc):
    """assets_repository.update returns Optional — the row can be soft-deleted
    between _require and the UPDATE. That must surface as 404, not as a
    TypeError from feeding None into _derived (a 500)."""
    a = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="prop", name="Blade"), USER
    )
    inner = svc.assets.update

    async def racing_update(asset_id, scope_id, fields):
        svc.assets.rows.pop(int(asset_id), None)  # concurrent soft delete
        return await inner(asset_id, scope_id, fields)

    svc.assets.update = racing_update
    with pytest.raises(AssetError) as ei:
        await svc.update_asset(int(a["id"]), SCOPE, AssetUpdate(description="x"))
    assert ei.value.status == 404 and ei.value.code == "asset_not_found"


@pytest.mark.asyncio
async def test_detach_file_404_when_not_attached(svc):
    a = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="A"), USER
    )
    rid = 727145299382534146
    await svc.attach_file(
        int(a["id"]), SCOPE, AttachFileRequest(resource_id=str(rid), slot="sheet"), USER
    )
    await svc.detach_file(int(a["id"]), SCOPE, rid, "sheet")  # positive control
    assert svc.relations.files == []
    with pytest.raises(AssetError) as ei:
        await svc.detach_file(int(a["id"]), SCOPE, rid, "sheet")
    assert ei.value.status == 404 and ei.value.code == "file_not_attached"


@pytest.mark.asyncio
async def test_remove_link_404_when_link_absent(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    robe = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="costume", name="Robe"), USER
    )
    with pytest.raises(AssetError) as ei:
        await svc.remove_link(int(c["id"]), SCOPE, int(robe["id"]), "wears")
    assert ei.value.status == 404 and ei.value.code == "link_not_found"
    assert svc.relations.strip_calls == []  # no link removed → nothing stripped


@pytest.mark.asyncio
async def test_remove_link_strips_costume_for_wears(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    robe = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="costume", name="Robe"), USER
    )
    await svc.add_link(
        int(c["id"]), SCOPE, LinkRequest(to_asset_id=robe["id"], relation="wears")
    )
    await svc.remove_link(int(c["id"]), SCOPE, int(robe["id"]), "wears")
    # (asset_id, costume_id, prop_id) — a costume must never arrive as prop_id.
    assert svc.relations.strip_calls == [(int(c["id"]), int(robe["id"]), None)]


@pytest.mark.asyncio
async def test_remove_link_strips_prop_for_holds(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    blade = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="prop", name="Blade"), USER
    )
    await svc.add_link(
        int(c["id"]), SCOPE, LinkRequest(to_asset_id=blade["id"], relation="holds")
    )
    await svc.remove_link(int(c["id"]), SCOPE, int(blade["id"]), "holds")
    assert svc.relations.strip_calls == [(int(c["id"]), None, int(blade["id"]))]


@pytest.mark.asyncio
async def test_unlink_project_404_when_not_linked(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    await svc.link_project(int(c["id"]), SCOPE, 55, USER)
    await svc.unlink_project(int(c["id"]), SCOPE, 55)  # positive control
    assert svc.relations.refs == []
    with pytest.raises(AssetError) as ei:
        await svc.unlink_project(int(c["id"]), SCOPE, 55)
    assert ei.value.status == 404 and ei.value.code == "project_ref_not_found"


# ── response contract ──────────────────────────────────────────────────────
#
# The routes carry no ``response_model`` (an Envelope[T] wrapper is deferred),
# so nothing else pins the wire shape against the declared schemas. Without
# this, AssetResponse & friends are referenced only by their own unit tests and
# could drift from what the service actually emits and the router serialises —
# the "backend returns a field the schema never described" failure mode, which
# produces no signal at all until a consumer reads the missing key.


@pytest.mark.asyncio
async def test_service_payloads_satisfy_declared_response_schemas(svc):
    from app.schemas.assets import (
        AssetDetailResponse,
        AssetFileResponse,
        AssetLinkResponse,
        AssetResponse,
        LoadoutResponse,
    )

    created = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="Sang Yao"), USER
    )
    AssetResponse.model_validate(created)
    aid = int(created["id"])

    costume = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="costume", name="Black Robe"), USER
    )
    AssetResponse.model_validate(costume)

    attached = await svc.attach_file(
        aid,
        SCOPE,
        AttachFileRequest(resource_id="727145299382534146", slot="sheet"),
        USER,
    )
    AssetFileResponse.model_validate(attached)

    linked = await svc.add_link(
        aid, SCOPE, LinkRequest(to_asset_id=costume["id"], relation="wears")
    )
    AssetLinkResponse.model_validate(linked)

    loadout = await svc.create_loadout(
        aid, SCOPE, LoadoutCreate(name="Battle", costume_ids=[costume["id"]])
    )
    LoadoutResponse.model_validate(loadout)

    detail = await svc.get_asset(aid, SCOPE)
    parsed = AssetDetailResponse.model_validate(detail)
    # The nested collections are the part a bare AssetResponse would not cover.
    assert len(parsed.files) == 1 and len(parsed.links) == 1
    assert len(parsed.loadouts) == 2  # Default + Battle
    assert parsed.linked_by == []


# ── I1: system presets are read-only on EVERY mutating path ────────────────


async def _preset(svc, asset_type="prompt", name="Grid"):
    """A row reachable from SCOPE only because ``get()`` unions presets in."""
    p = await svc.create_asset(
        SCOPE,
        AssetCreate(asset_type=asset_type, name=name),
        USER,
    )
    svc.assets.make_preset(p["id"])
    return int(p["id"])


@pytest.mark.asyncio
async def test_attach_file_refuses_system_preset(svc):
    pid = await _preset(svc)
    with pytest.raises(AssetError) as ei:
        await svc.attach_file(
            pid,
            SCOPE,
            # 'examples' IS a valid prompt slot — the refusal must come from the
            # preset gate, not from slot validation.
            AttachFileRequest(resource_id="727145299382534146", slot="examples"),
            USER,
        )
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"
    assert svc.relations.files == []


@pytest.mark.asyncio
async def test_detach_file_refuses_system_preset(svc):
    pid = await _preset(svc)
    with pytest.raises(AssetError) as ei:
        await svc.detach_file(pid, SCOPE, 727145299382534146, "examples")
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"


@pytest.mark.asyncio
async def test_add_link_refuses_system_preset(svc):
    """The preset is the LINK SOURCE: without the gate, team T writes an
    asset_links row on the global asset and team U reads the target id back."""
    pid = await _preset(svc, "audio", "Preset Ambience")
    loc = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="location", name="Rooftop"), USER
    )
    with pytest.raises(AssetError) as ei:
        await svc.add_link(
            pid, SCOPE, LinkRequest(to_asset_id=loc["id"], relation="ambience_of")
        )
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"
    assert svc.relations.links == []


@pytest.mark.asyncio
async def test_remove_link_refuses_system_preset(svc):
    pid = await _preset(svc, "audio", "Preset Ambience")
    with pytest.raises(AssetError) as ei:
        await svc.remove_link(pid, SCOPE, 12345, "ambience_of")
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"


@pytest.mark.asyncio
async def test_create_loadout_refuses_system_preset(svc):
    pid = await _preset(svc, "character", "Preset Hero")
    before = len(await svc.relations.list_loadouts(pid))
    with pytest.raises(AssetError) as ei:
        await svc.create_loadout(pid, SCOPE, LoadoutCreate(name="Night"))
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"
    assert len(await svc.relations.list_loadouts(pid)) == before


@pytest.mark.asyncio
async def test_update_loadout_refuses_system_preset(svc):
    pid = await _preset(svc, "character", "Preset Hero")
    lo = (await svc.relations.list_loadouts(pid))[0]
    with pytest.raises(AssetError) as ei:
        await svc.update_loadout(pid, SCOPE, int(lo["id"]), LoadoutUpdate(name="X"))
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"
    assert lo["name"] == "Default"


@pytest.mark.asyncio
async def test_delete_loadout_refuses_system_preset(svc):
    pid = await _preset(svc, "character", "Preset Hero")
    lo = (await svc.relations.list_loadouts(pid))[0]
    with pytest.raises(AssetError) as ei:
        await svc.delete_loadout(pid, SCOPE, int(lo["id"]))
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"
    assert len(await svc.relations.list_loadouts(pid)) == 1


@pytest.mark.asyncio
async def test_link_project_refuses_system_preset(svc):
    pid = await _preset(svc)
    with pytest.raises(AssetError) as ei:
        await svc.link_project(pid, SCOPE, 55, USER)
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"
    assert svc.relations.refs == []


@pytest.mark.asyncio
async def test_unlink_project_refuses_system_preset(svc):
    pid = await _preset(svc)
    with pytest.raises(AssetError) as ei:
        await svc.unlink_project(pid, SCOPE, 55)
    assert ei.value.status == 403 and ei.value.code == "system_preset_readonly"


# ── I2: a personal project's scope is its OWNER's personal team ────────────


@pytest.mark.asyncio
async def test_link_personal_project_requires_owners_personal_team(svc, monkeypatch):
    """A collaborator on someone else's personal project used to link an asset
    from their OWN team: 201 for a row GET /projects/{id}/assets can never
    return, because the read side resolves the owner's team."""
    import app.services.assets.assets_service as m

    seen = []

    async def _resolver(user_id):
        seen.append(user_id)
        return "999"  # the owner's personal team — NOT the caller's SCOPE

    monkeypatch.setattr(m, "_resolve_personal_team_id", _resolver)
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    with pytest.raises(AssetError) as ei:
        await svc.link_project(int(c["id"]), SCOPE, 58, USER)
    assert ei.value.status == 422 and ei.value.code == "project_scope_mismatch"
    assert seen == [OTHER_OWNER], "resolved the caller's team, not the owner's"
    assert svc.relations.refs == []


@pytest.mark.asyncio
async def test_link_personal_project_ok_when_owner_team_is_the_scope(svc, monkeypatch):
    import app.services.assets.assets_service as m

    async def _resolver(user_id):
        return str(SCOPE)

    monkeypatch.setattr(m, "_resolve_personal_team_id", _resolver)
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    await svc.link_project(int(c["id"]), SCOPE, 58, USER)
    assert (int(c["id"]), 58) in svc.relations.refs


@pytest.mark.asyncio
async def test_link_personal_project_without_owner_is_typed_not_500(svc, monkeypatch):
    """Legacy rows: no team AND no resolvable personal team must answer 422,
    not let a ValueError out as an untyped 500."""
    import app.services.assets.assets_service as m

    async def _boom(user_id):
        raise ValueError("No personal team found")

    monkeypatch.setattr(m, "_resolve_personal_team_id", _boom)
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    with pytest.raises(AssetError) as ei:
        await svc.link_project(int(c["id"]), SCOPE, 58, USER)
    assert ei.value.status == 422 and ei.value.code == "project_scope_mismatch"
    with pytest.raises(AssetError) as ei:  # owner_id NULL
        await svc.link_project(int(c["id"]), SCOPE, 59, USER)
    assert ei.value.status == 422 and ei.value.code == "project_scope_mismatch"


# ── M3: the asset and its Default loadout are ONE transaction ──────────────


@pytest.fixture
def uow_spy(monkeypatch):
    """Record every entry/exit of the service's unit-of-work.

    The fakes hold rows in a dict, so nothing here can roll anything back —
    that is an engine property, pinned against a real server in
    ``tests/db/test_assets_repository_integration.py``. What IS checkable
    without a DB is the part the engine cannot fix for us: that both writes are
    issued INSIDE one open block. A create moved back out of it would still
    pass every other test in this file.
    """
    import contextlib

    import app.services.assets.assets_service as m

    log: list[str] = []

    @contextlib.asynccontextmanager
    async def _spy(enabled):
        log.append(f"uow_enter(enabled={enabled})")
        try:
            yield None
        finally:
            log.append("uow_exit")

    monkeypatch.setattr(m, "maybe_unit_of_work", _spy)
    monkeypatch.setattr(m, "is_configured", lambda: True)
    return log


def _traced(svc, log):
    """Append repo calls to the same log the uow spy writes to."""
    real_create = svc.assets.create
    real_loadout = svc.relations.create_loadout

    async def create(scope_id, fields, created_by):
        log.append("assets.create")
        return await real_create(scope_id, fields, created_by)

    async def create_loadout(asset_id, fields):
        log.append("relations.create_loadout")
        return await real_loadout(asset_id, fields)

    svc.assets.create = create
    svc.relations.create_loadout = create_loadout


@pytest.mark.asyncio
async def test_character_create_and_default_loadout_share_one_uow(svc, uow_spy):
    _traced(svc, uow_spy)
    await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="Sang Yao"), USER
    )
    assert uow_spy == [
        "uow_enter(enabled=True)",
        "assets.create",
        "relations.create_loadout",
        "uow_exit",
    ]


@pytest.mark.asyncio
async def test_a_failing_default_loadout_takes_the_asset_down_with_it(svc, uow_spy):
    """The bug: two transactions meant a committed character with no loadout —
    the one state ``create_loadout`` exists to make impossible — while the user
    saw only an error. The error must still reach the caller (no silent
    no-op), and the write must still be inside the open block when it does.
    """

    async def _boom(asset_id, fields):
        uow_spy.append("relations.create_loadout")
        raise RuntimeError("loadout insert failed")

    _traced(svc, uow_spy)
    svc.relations.create_loadout = _boom

    with pytest.raises(RuntimeError):
        await svc.create_asset(
            SCOPE, AssetCreate(asset_type="character", name="Doomed"), USER
        )
    # The raise happened while the transaction was still open — that, not the
    # fake's dict, is what a real engine turns into a rollback.
    assert uow_spy.index("relations.create_loadout") < uow_spy.index("uow_exit")
    assert uow_spy[-1] == "uow_exit"


@pytest.mark.asyncio
async def test_non_character_create_still_opens_the_uow(svc, uow_spy):
    """A prop writes one row, so atomicity is trivially satisfied — but the
    block must not be conditional on the asset type: a later second write added
    to this path would then be atomic for characters only."""
    _traced(svc, uow_spy)
    await svc.create_asset(SCOPE, AssetCreate(asset_type="prop", name="Blade"), USER)
    assert uow_spy == ["uow_enter(enabled=True)", "assets.create", "uow_exit"]


@pytest.mark.asyncio
async def test_duplicate_is_answered_before_the_insert(svc, uow_spy):
    """Inside a transaction the 409 must be decided by a SELECT that runs
    BEFORE the failing INSERT: afterwards the transaction is aborted and the
    lookup ``AssetsRepository.create`` used to do raises PendingRollbackError —
    an untyped 500 in place of the 409 (integration case 15's finding, now
    reachable from this path too because it opens a UoW).
    """
    a = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="location", name="Bamboo Grove"), USER
    )
    _traced(svc, uow_spy)
    with pytest.raises(AssetError) as ei:
        await svc.create_asset(
            SCOPE, AssetCreate(asset_type="location", name="bamboo grove"), USER
        )
    assert ei.value.status == 409 and ei.value.code == "asset_exists"
    assert ei.value.extra["existing_asset_id"] == a["id"]
    # Decided without ever reaching the INSERT.
    assert "assets.create" not in uow_spy


@pytest.mark.asyncio
async def test_uow_is_skipped_when_no_engine_exists(svc, monkeypatch):
    """``maybe_``, not the bare form: this service also runs in processes that
    never had an engine (this very suite). ``enabled=False`` must reach
    ``maybe_unit_of_work`` so it stays a no-op instead of raising."""
    import contextlib

    import app.services.assets.assets_service as m

    seen: list[bool] = []

    @contextlib.asynccontextmanager
    async def _spy(enabled):
        seen.append(enabled)
        yield None

    monkeypatch.setattr(m, "maybe_unit_of_work", _spy)
    monkeypatch.setattr(m, "is_configured", lambda: False)
    await svc.create_asset(SCOPE, AssetCreate(asset_type="prop", name="Blade"), USER)
    assert seen == [False]


# ── C-1: an ambient transaction is JOINED, never nested inside ─────────────


@pytest.fixture
def forbid_new_transaction(monkeypatch):
    """Make opening a NEW session loud instead of silent.

    ``unit_of_work()`` reaches for ``get_sessionmaker()`` as its first act, so
    a sessionmaker that raises turns "a second transaction was opened" into a
    failure with a name on it. Returns a flag list the negative control reads,
    proving the probe can actually fire.
    """
    import app.db.session as sess

    tripped: list[str] = []

    def _boom():
        tripped.append("opened")
        raise AssertionError("opened a second transaction inside an ambient one")

    monkeypatch.setattr(sess, "get_sessionmaker", _boom)
    return tripped


@pytest.fixture
def ambient_uow(monkeypatch):
    """Pretend a caller already has a transaction open (what
    ``generated_inbox_service._save_as_asset_core`` really does around
    ``create_asset``) without needing an engine to open a real one."""
    import app.db.session as sess

    token = sess._request_session.set(object())  # type: ignore[arg-type]
    yield
    sess._request_session.reset(token)


@pytest.mark.asyncio
async def test_create_asset_joins_an_outer_uow_instead_of_nesting(
    svc, monkeypatch, ambient_uow, forbid_new_transaction
):
    """The regression this pins is invisible to every other test.

    ``unit_of_work()`` nested in an active one is REQUIRES_NEW: the inner block
    COMMITS INDEPENDENTLY and an outer rollback does not undo it. With
    ``create_asset`` opening its own, a ``_save_as_asset_core`` whose
    ``attach_file`` or ``set_review_state`` then failed would roll back around
    an already-committed asset — an orphan with no attachment, which the user's
    retry meets as a 409 asset_exists. That path's own suite replaces
    ``AssetsService`` with a fake, so nothing there executes this method.
    """
    import app.services.assets.assets_service as m

    monkeypatch.setattr(m, "is_configured", lambda: True)
    a = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="Sang Yao"), USER
    )
    # The writes still happened — joining is not skipping.
    assert a["id"].isdigit()
    los = await svc.relations.list_loadouts(int(a["id"]))
    assert len(los) == 1 and los[0]["is_default"]
    assert forbid_new_transaction == []


@pytest.mark.asyncio
async def test_the_probe_fires_when_a_transaction_really_is_opened(
    svc, monkeypatch, forbid_new_transaction
):
    """Negative control for the test above: WITHOUT an ambient transaction the
    same call does open one, so the empty list up there is the guard working
    and not a probe that never fires."""
    import app.services.assets.assets_service as m

    monkeypatch.setattr(m, "is_configured", lambda: True)
    with pytest.raises(AssertionError, match="second transaction"):
        await svc.create_asset(
            SCOPE, AssetCreate(asset_type="character", name="Sang Yao"), USER
        )
    assert forbid_new_transaction == ["opened"]


@pytest.mark.asyncio
async def test_duplicate_joins_an_outer_uow_instead_of_nesting(
    svc, monkeypatch, ambient_uow, forbid_new_transaction
):
    """Same shape as ``create_asset``. Only the router calls ``duplicate``
    today, so this is the potential case rather than the in-flight one — but
    the failure it prevents (a copy that survives its caller's rollback) is
    silent, so the guard is pinned rather than left to a future reader."""
    import app.services.assets.assets_service as m
    from app.schemas.assets import DuplicateRequest

    monkeypatch.setattr(m, "is_configured", lambda: True)
    src = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="Original"), USER
    )
    copy = await svc.duplicate(int(src["id"]), SCOPE, USER, DuplicateRequest())
    assert copy["name"] == "Original (copy)"
    assert forbid_new_transaction == []


@pytest.mark.asyncio
async def test_race_409_omits_the_id_it_could_not_learn(svc, uow_spy):
    """I-2. Inside a transaction the repo cannot resolve the winner's id (the
    failed INSERT aborted it) and reports 0. Forwarding that would give both
    dialogs — SaveAsAssetDialog's setExistingConflictId, NewAssetDialog's
    setExistingId — a recovery link pointing at asset 0. The key must be
    ABSENT, which is what ``duplicate`` already does for the same case."""
    from app.repositories.assets_repository import DuplicateAssetName

    async def _lost_race(scope_id, fields, created_by):
        raise DuplicateAssetName(existing_id=0)

    svc.assets.create = _lost_race
    with pytest.raises(AssetError) as ei:
        await svc.create_asset(
            SCOPE, AssetCreate(asset_type="location", name="Bamboo Grove"), USER
        )
    assert ei.value.status == 409 and ei.value.code == "asset_exists"
    assert "existing_asset_id" not in ei.value.extra, ei.value.extra


@pytest.mark.asyncio
async def test_race_409_still_carries_a_real_id_when_it_has_one(svc, uow_spy):
    """The other half: omitting the key is for the UNKNOWN case only. A repo
    that did resolve the winner must still hand the dialog its jump target."""
    from app.repositories.assets_repository import DuplicateAssetName

    async def _lost_race(scope_id, fields, created_by):
        raise DuplicateAssetName(existing_id=4242)

    svc.assets.create = _lost_race
    with pytest.raises(AssetError) as ei:
        await svc.create_asset(
            SCOPE, AssetCreate(asset_type="location", name="Bamboo Grove"), USER
        )
    assert ei.value.extra["existing_asset_id"] == "4242"


# ── P4: used_in — the canvas mirror, scope-limited ─────────────────────────


@pytest.mark.asyncio
async def test_get_detail_carries_used_in_with_both_halves(svc):
    """``used_in.storyboards`` is present and empty on purpose (no storyboard
    mirror yet). Absent and empty are different answers: the sheet renders "no
    usage" for the second and throws on the first."""
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    svc.canvas_refs.rows = [
        {
            "canvas_id": "5001",
            "canvas_name": "Looks",
            "kind": "smart",
            "project_id": "9000",
            "node_ids": ["asset-1"],
            "loadout_ids": [],
        }
    ]

    d = await svc.get_asset(int(c["id"]), SCOPE, include_used_in=True)

    assert d["used_in"]["canvases"] == svc.canvas_refs.rows
    assert d["used_in"]["storyboards"] == []


@pytest.mark.asyncio
async def test_get_detail_scopes_the_used_in_read_to_the_caller(svc):
    """A system preset is readable from EVERY scope, so an unscoped read here
    would answer with other teams' canvas names on a row anyone can fetch."""
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    await svc.get_asset(int(c["id"]), SCOPE, include_used_in=True)
    assert svc.canvas_refs.calls == [(c["id"], str(SCOPE))]


@pytest.mark.asyncio
async def test_list_canvas_refs_404s_instead_of_answering_an_empty_list(svc):
    """ "Not yours" must not be reported as "unused" — the caller cannot tell
    those apart from a 200 with `[]`, and would render an empty usage panel for
    an asset they have no access to."""
    with pytest.raises(AssetError) as ei:
        await svc.list_canvas_refs(999, SCOPE)
    assert ei.value.status == 404 and ei.value.code == "asset_not_found"
    assert svc.canvas_refs.calls == []  # never reached the mirror


@pytest.mark.asyncio
async def test_list_canvas_refs_returns_the_same_rows_used_in_carries(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    svc.canvas_refs.rows = [{"canvas_id": "5001", "node_ids": ["asset-1"]}]

    rows = await svc.list_canvas_refs(int(c["id"]), SCOPE)
    detail = await svc.get_asset(int(c["id"]), SCOPE, include_used_in=True)

    assert rows == detail["used_in"]["canvases"]


@pytest.mark.asyncio
async def test_detail_response_declares_every_key_get_asset_actually_emits(svc):
    """The derived-key guard, DERIVED — not a hand-listed set.

    ``Envelope[AssetDetailResponse]`` silently DROPS any key the model does not
    declare, so a sixth relation bolted onto ``get_asset`` would never reach the
    client and nothing would fail. Its sibling in ``test_schemas.py`` takes the
    asset ROW's expectation from ``Assets.__table__.columns``; these keys are not
    columns of anything, so the only honest source is the method itself. Driving
    it here — where the fakes live — means a new key extends this test by
    existing, instead of needing someone to remember to add its name.
    """
    from app.schemas.assets import AssetDetailResponse

    # The ONE key the model drops on purpose, same exclusion (and same reason)
    # as the AssetResponse column pin in test_schemas.py: every read filters
    # ``deleted_at IS NULL``, so a row reaching a response always has it null
    # and it carries no information. Spelled as a one-name allowlist rather
    # than a hand-listed expectation, so a SEVENTH key still fails here.
    DELIBERATELY_UNDECLARED = {"deleted_at"}

    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    # The RICHEST call — every optional half asked for — because the guard is
    # about keys the response model would DROP, and a key can only be dropped
    # if it was emitted. Running the default (no ``used_in``) would silently
    # shrink what this test inspects.
    emitted = set(await svc.get_asset(int(c["id"]), SCOPE, include_used_in=True))

    missing = emitted - set(AssetDetailResponse.model_fields) - DELIBERATELY_UNDECLARED
    assert not missing, (
        "get_asset emits keys AssetDetailResponse does not declare; the response "
        f"model will drop them on the way out: {sorted(missing)}"
    )
    # Positive control: the guard is only meaningful if the emitted set really
    # contains the derived relations, not just the plain column names.
    assert {"files", "links", "linked_by", "loadouts", "used_in"} <= emitted


# ── used_in is OPT-IN (I2) ─────────────────────────────────────────────────
#
# `used_in.canvases` is a five-table aggregate, and the canvas puts dozens of
# asset cards on one board, each fetching its own detail on mount. None of them
# renders usage. These pin that the default costs nothing and reports honestly.


@pytest.mark.asyncio
async def test_the_default_detail_does_not_read_the_canvas_mirror(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )

    await svc.get_asset(int(c["id"]), SCOPE)

    assert svc.canvas_refs.calls == []


@pytest.mark.asyncio
async def test_used_in_is_absent_not_empty_when_it_was_not_asked_for(svc):
    """ "Nobody looked" and "used nowhere" are different facts. An empty
    ``used_in`` is a claim, and a caller that skipped the aggregate has no
    basis for it — the sheet would render "Used nowhere" for an answer that was
    never computed."""
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    svc.canvas_refs.rows = [{"canvas_id": "5001", "node_ids": ["asset-1"]}]

    d = await svc.get_asset(int(c["id"]), SCOPE)

    assert "used_in" not in d


@pytest.mark.asyncio
async def test_asking_for_used_in_still_answers_both_halves(svc):
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )

    d = await svc.get_asset(int(c["id"]), SCOPE, include_used_in=True)

    assert d["used_in"] == {"canvases": [], "storyboards": []}
    assert svc.canvas_refs.calls == [(c["id"], str(SCOPE))]


@pytest.mark.asyncio
async def test_the_split_out_endpoint_is_unaffected_by_the_flag(svc):
    """``GET /assets/{id}/canvas-refs`` exists so a caller can ask for usage on
    its own. It has no flag and never had one."""
    c = await svc.create_asset(
        SCOPE, AssetCreate(asset_type="character", name="C"), USER
    )
    svc.canvas_refs.rows = [{"canvas_id": "5001", "node_ids": ["asset-1"]}]

    rows = await svc.list_canvas_refs(int(c["id"]), SCOPE)

    assert rows == svc.canvas_refs.rows
