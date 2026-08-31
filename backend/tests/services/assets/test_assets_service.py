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
        assets_scope_or_preset CHECK forbids any other combination."""
        row = self.rows[int(asset_id)]
        row["is_system_preset"] = True
        row["scope_id"] = None
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
        self.refs.append((a, p))
        return True

    async def unlink_project(self, a, p):
        before = len(self.refs)
        self.refs = [r for r in self.refs if r != (a, p)]
        return len(self.refs) < before

    async def list_project_ids(self, a):
        return [p for (x, p) in self.refs if x == a]


@pytest.fixture
def svc():
    return AssetsService(
        assets_repo=FakeAssetsRepo(), relations_repo=FakeRelationsRepo()
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
        AssetCreate(asset_type="prompt", name="Grid", source="system_preset"),
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
        AssetCreate(asset_type="prompt", name="Grid", source="system_preset"),
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
        AssetCreate(asset_type=asset_type, name=name, source="system_preset"),
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
