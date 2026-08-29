"""AssetsService invariants with in-memory fake repos (no DB)."""

from __future__ import annotations

import datetime

import pytest

from app.repositories.assets_repository import DuplicateAssetName
from app.schemas.assets import (
    AssetCreate,
    AttachFileRequest,
    LinkRequest,
    LoadoutCreate,
    LoadoutUpdate,
)
from app.services.assets.assets_service import AssetError, AssetsService

NOW = datetime.datetime(2026, 8, 28, tzinfo=datetime.timezone.utc)
SCOPE = 727145299382534200
USER = "11111111-1111-1111-1111-111111111111"


class FakeAssetsRepo:
    def __init__(self):
        self.rows: dict[int, dict] = {}
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

    async def get(self, asset_id, scope_id):
        r = self.rows.get(int(asset_id))
        return r if r and r["scope_id"] == scope_id else None

    async def list(self, scope_id, **kw):
        return [r for r in self.rows.values() if r["scope_id"] == scope_id]

    async def update(self, asset_id, scope_id, fields):
        self.rows[int(asset_id)].update(fields)
        return self.rows[int(asset_id)]

    async def soft_delete(self, asset_id, scope_id):
        return self.rows.pop(int(asset_id), None) is not None

    async def slot_counts(self, ids):
        return {}

    async def project_ids(self, ids):
        return {}

    async def loadout_counts(self, ids):
        return {}


class FakeRelationsRepo:
    def __init__(self):
        self.files, self.links, self.loadouts, self.refs = [], [], {}, []
        self._next = 5000
        self.in_scope_resources = {727145299382534146}

    async def resource_in_scope(self, resource_id, scope_id):
        return int(resource_id) in self.in_scope_resources

    async def attach(self, asset_id, resource_id, slot, **kw):
        row = {
            "asset_id": asset_id,
            "resource_id": resource_id,
            "slot": slot,
            "loadout_id": kw.get("loadout_id"),
            "sort_order": 0,
            "note": kw.get("note"),
            "attached_by": kw.get("attached_by"),
            "attached_at": NOW,
        }
        self.files.append(row)
        return row

    async def detach(self, asset_id, resource_id, slot):
        return True

    async def list_files(self, asset_id):
        return [f for f in self.files if f["asset_id"] == asset_id]

    async def add_link(self, f, t, rel):
        row = {"from_asset_id": f, "to_asset_id": t, "relation": rel, "created_at": NOW}
        self.links.append(row)
        return row

    async def remove_link(self, f, t, rel):
        return True

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
        self.loadouts[lid].update(fields)
        return self.loadouts[lid]

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
        return 0

    project_teams = {55: SCOPE, 56: 999}

    async def project_team_id(self, project_id):
        if project_id not in self.project_teams:
            return False, None
        return True, self.project_teams[project_id]

    async def link_project(self, a, p, u):
        self.refs.append((a, p))
        return True

    async def unlink_project(self, a, p):
        return True

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
async def test_update_loadout_set_default_on_foreign_loadout_404(svc):
    """set_default returns False when the loadout is another asset's — 404, and
    the foreign asset keeps its own default (nothing was written)."""
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
    svc.assets.rows[int(p["id"])]["is_system_preset"] = True
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
    svc.assets.rows[int(p["id"])]["is_system_preset"] = True
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
