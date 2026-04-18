"""Unit tests for ScriptService orchestration logic."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app.services import script_service as svc_module
from app.services.script_service import ScriptService


class _FakeProjectRepo:
    def __init__(self) -> None:
        self.projects: dict[str, dict[str, Any]] = {}
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.soft_deletes: list[str] = []
        self.create_returns: dict[str, Any] = {}

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        proj = {"id": "sp-1", **data}
        self.projects["sp-1"] = proj
        return proj

    async def get_by_id(self, script_id: str) -> Optional[dict[str, Any]]:
        return self.projects.get(script_id)

    async def update(
        self, script_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        self.updates.append((script_id, data))
        existing = self.projects.get(script_id, {"id": script_id})
        merged = {**existing, **data}
        self.projects[script_id] = merged
        return merged

    async def list_by_project(self, **kwargs: Any) -> dict[str, Any]:
        return {"items": [], "total": 0, **kwargs}

    async def soft_delete(self, script_id: str) -> None:
        self.soft_deletes.append(script_id)


class _FakeChapterRepo:
    def __init__(self) -> None:
        self.chapters: dict[str, list[dict[str, Any]]] = {}
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []
        self.bulk_upserts: list[tuple[str, list[dict[str, Any]]]] = []

    async def get_by_script(self, script_id: str) -> list[dict[str, Any]]:
        return self.chapters.get(script_id, [])

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        self.created.append(data)
        return {"id": "ch-new", **data}

    async def update(
        self, chapter_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        self.updated.append((chapter_id, data))
        return {"id": chapter_id, **data}

    async def delete(self, chapter_id: str) -> None:
        self.deleted.append(chapter_id)

    async def bulk_upsert(
        self, script_id: str, rows: list[dict[str, Any]]
    ) -> None:
        self.bulk_upserts.append((script_id, rows))


class _FakeAssetRepo:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []
        self.assets_by_script: dict[str, list[dict[str, Any]]] = {}

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        self.created.append(data)
        return {"id": "a-new", **data}

    async def update(self, asset_id: str, data: dict[str, Any]) -> dict[str, Any]:
        self.updated.append((asset_id, data))
        return {"id": asset_id, **data}

    async def delete(self, asset_id: str) -> None:
        self.deleted.append(asset_id)

    async def list_by_script(
        self, script_id: str, asset_type: Optional[str] = None
    ) -> list[dict[str, Any]]:
        rows = self.assets_by_script.get(script_id, [])
        if asset_type:
            rows = [r for r in rows if r.get("asset_type") == asset_type]
        return rows


class _FakeLinkRepo:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.links_by_chapter: dict[str, list[dict[str, Any]]] = {}

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        self.created.append(data)
        return {"id": "l-new", **data}

    async def delete(self, link_id: str) -> None:
        self.deleted.append(link_id)

    async def list_by_chapter(self, chapter_id: str) -> list[dict[str, Any]]:
        return self.links_by_chapter.get(chapter_id, [])


@pytest.fixture
def service() -> tuple[ScriptService, _FakeProjectRepo, _FakeChapterRepo, _FakeAssetRepo, _FakeLinkRepo]:
    svc = ScriptService()
    p, c, a, l = _FakeProjectRepo(), _FakeChapterRepo(), _FakeAssetRepo(), _FakeLinkRepo()
    svc.project_repo = p  # type: ignore[assignment]
    svc.chapter_repo = c  # type: ignore[assignment]
    svc.asset_repo = a  # type: ignore[assignment]
    svc.link_repo = l  # type: ignore[assignment]
    return svc, p, c, a, l


# ─── Project ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_project_stamps_display_code(
    service: tuple[ScriptService, _FakeProjectRepo, Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, project_repo, *_ = service

    async def _fake_generate_display_code(team_id: int, prefix: str) -> str:
        return "S-202604001"

    monkeypatch.setattr(
        svc_module, "generate_display_code", _fake_generate_display_code
    )

    result = await svc.create_project(
        team_id="42", user_id="u-1", project_id=100, name="My Script"
    )
    assert result["display_code"] == "S-202604001"
    # display_code update was written via project_repo.update
    assert any(
        "display_code" in data for _, data in project_repo.updates
    )


@pytest.mark.asyncio
async def test_create_project_survives_display_code_failure(
    service: tuple[ScriptService, _FakeProjectRepo, Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, project_repo, *_ = service

    async def _raising(team_id: int, prefix: str) -> str:
        raise RuntimeError("rpc down")

    monkeypatch.setattr(svc_module, "generate_display_code", _raising)

    result = await svc.create_project(
        team_id="42", user_id="u-1", project_id=100, name="ok"
    )
    # Without display_code update, but the create still succeeded
    assert result["name"] == "ok"


@pytest.mark.asyncio
async def test_get_project_full_returns_none_when_missing(
    service: tuple[ScriptService, _FakeProjectRepo, Any, Any, Any],
) -> None:
    svc, *_ = service
    assert await svc.get_project_full("missing") is None


@pytest.mark.asyncio
async def test_get_project_full_merges_chapters(
    service: tuple[ScriptService, _FakeProjectRepo, _FakeChapterRepo, Any, Any],
) -> None:
    svc, project_repo, chapter_repo, *_ = service
    project_repo.projects["sp-1"] = {"id": "sp-1", "name": "Hello"}
    chapter_repo.chapters["sp-1"] = [{"id": "ch-1"}, {"id": "ch-2"}]

    result = await svc.get_project_full("sp-1")
    assert result is not None
    assert result["project"]["name"] == "Hello"
    assert len(result["chapters"]) == 2


@pytest.mark.asyncio
async def test_update_viewport_writes_viewport_json(
    service: tuple[ScriptService, _FakeProjectRepo, Any, Any, Any],
) -> None:
    svc, project_repo, *_ = service
    await svc.update_viewport("sp-1", {"x": 10, "y": 20, "zoom": 1.5})
    assert project_repo.updates == [("sp-1", {"viewport_json": {"x": 10, "y": 20, "zoom": 1.5}})]


@pytest.mark.asyncio
async def test_soft_delete_project(
    service: tuple[ScriptService, _FakeProjectRepo, Any, Any, Any],
) -> None:
    svc, project_repo, *_ = service
    await svc.soft_delete_project("sp-1")
    assert project_repo.soft_deletes == ["sp-1"]


# ─── Chapters ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_chapter_attaches_script_id(
    service: tuple[ScriptService, Any, _FakeChapterRepo, Any, Any],
) -> None:
    svc, _, chapter_repo, *_ = service
    await svc.create_chapter("sp-1", {"title": "Intro"})
    assert chapter_repo.created[0]["script_id"] == "sp-1"
    assert chapter_repo.created[0]["title"] == "Intro"


@pytest.mark.asyncio
async def test_sync_canvas_applies_delete_create_update_then_returns_chapters(
    service: tuple[ScriptService, Any, _FakeChapterRepo, Any, Any],
) -> None:
    svc, _, chapter_repo, *_ = service
    chapter_repo.chapters["sp-1"] = [{"id": "ch-final"}]

    result = await svc.sync_canvas(
        "sp-1",
        added=[{"title": "New"}],
        updated=[{"id": "ch-existing", "title": "Renamed"}],
        deleted_ids=["ch-old"],
    )
    assert chapter_repo.deleted == ["ch-old"]
    assert chapter_repo.bulk_upserts == [("sp-1", [{"title": "New"}])]
    assert chapter_repo.updated == [("ch-existing", {"title": "Renamed"})]
    assert result == {"chapters": [{"id": "ch-final"}]}


@pytest.mark.asyncio
async def test_sync_canvas_skips_updates_without_id(
    service: tuple[ScriptService, Any, _FakeChapterRepo, Any, Any],
) -> None:
    svc, _, chapter_repo, *_ = service
    chapter_repo.chapters["sp-1"] = []
    await svc.sync_canvas("sp-1", added=[], updated=[{"title": "no-id"}], deleted_ids=[])
    assert chapter_repo.updated == []


# ─── Assets ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_assets_filters_by_type(
    service: tuple[ScriptService, Any, Any, _FakeAssetRepo, Any],
) -> None:
    svc, *_, asset_repo, _ = service
    asset_repo.assets_by_script["sp-1"] = [
        {"id": "a1", "asset_type": "character"},
        {"id": "a2", "asset_type": "location"},
    ]
    rows = await svc.list_assets("sp-1", asset_type="character")
    assert [r["id"] for r in rows] == ["a1"]


@pytest.mark.asyncio
async def test_asset_crud_delegation(
    service: tuple[ScriptService, Any, Any, _FakeAssetRepo, Any],
) -> None:
    svc, *_, asset_repo, _ = service
    await svc.create_asset({"name": "Hero"})
    await svc.update_asset("a-1", {"name": "Hero v2"})
    await svc.delete_asset("a-1")
    assert asset_repo.created == [{"name": "Hero"}]
    assert asset_repo.updated == [("a-1", {"name": "Hero v2"})]
    assert asset_repo.deleted == ["a-1"]


# ─── Storyboard links ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_link_crud_delegation(
    service: tuple[ScriptService, Any, Any, Any, _FakeLinkRepo],
) -> None:
    svc, *_, link_repo = service
    await svc.create_storyboard_link({"chapter_id": "ch-1", "storyboard_id": "sb-1"})
    await svc.delete_storyboard_link("l-1")
    link_repo.links_by_chapter["ch-1"] = [{"id": "l-1"}]
    links = await svc.list_links_by_chapter("ch-1")
    assert link_repo.created[0]["storyboard_id"] == "sb-1"
    assert link_repo.deleted == ["l-1"]
    assert links == [{"id": "l-1"}]
