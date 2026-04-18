"""Unit tests for LibrariesService.

The service is a thin orchestration layer over LibrariesRepository —
tests verify the data shape it constructs and forwards.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app.services.libraries_service import LibrariesService


class _FakeRepo:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []
        self.library_by_id: dict[str, dict[str, Any]] = {}
        self.libraries_by_scope: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        self.created.append(data)
        return {"id": "lib-new", **data}

    async def list_by_scope(
        self, scope_type: str, scope_id: str
    ) -> list[dict[str, Any]]:
        return self.libraries_by_scope.get((scope_type, scope_id), [])

    async def get_by_id(self, library_id: str) -> Optional[dict[str, Any]]:
        return self.library_by_id.get(library_id)

    async def update(
        self, library_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        self.updated.append((library_id, data))
        return {"id": library_id, **data}

    async def delete(self, library_id: str) -> bool:
        self.deleted.append(library_id)
        return True


@pytest.fixture
def service() -> tuple[LibrariesService, _FakeRepo]:
    svc = LibrariesService()
    fake = _FakeRepo()
    svc.repo = fake  # type: ignore[assignment]
    return svc, fake


@pytest.mark.asyncio
async def test_create_library_sets_scope_type_team(
    service: tuple[LibrariesService, _FakeRepo],
) -> None:
    svc, fake = service
    await svc.create_library(
        user_id="u-1",
        name="Docs",
        scope_id="t-1",
        icon="book",
        color="#abc",
    )
    payload = fake.created[0]
    assert payload["scope_type"] == "team"
    assert payload["scope_id"] == "t-1"
    assert payload["created_by"] == "u-1"
    assert payload["name"] == "Docs"
    assert payload["icon"] == "book"
    assert payload["color"] == "#abc"


@pytest.mark.asyncio
async def test_create_library_accepts_nullable_icon_color(
    service: tuple[LibrariesService, _FakeRepo],
) -> None:
    svc, fake = service
    await svc.create_library(user_id="u", name="X", scope_id="t")
    payload = fake.created[0]
    assert payload["icon"] is None
    assert payload["color"] is None


@pytest.mark.asyncio
async def test_list_libraries_filters_by_scope(
    service: tuple[LibrariesService, _FakeRepo],
) -> None:
    svc, fake = service
    fake.libraries_by_scope[("team", "t-1")] = [{"id": "lib-1"}]
    rows = await svc.list_libraries("t-1")
    assert rows == [{"id": "lib-1"}]


@pytest.mark.asyncio
async def test_get_library_returns_none_for_unknown(
    service: tuple[LibrariesService, _FakeRepo],
) -> None:
    svc, _ = service
    assert await svc.get_library("missing") is None


@pytest.mark.asyncio
async def test_update_library_forwards_payload(
    service: tuple[LibrariesService, _FakeRepo],
) -> None:
    svc, fake = service
    await svc.update_library("lib-1", {"name": "Renamed"})
    assert fake.updated == [("lib-1", {"name": "Renamed"})]


@pytest.mark.asyncio
async def test_delete_library_returns_repo_bool(
    service: tuple[LibrariesService, _FakeRepo],
) -> None:
    svc, fake = service
    assert await svc.delete_library("lib-1") is True
    assert fake.deleted == ["lib-1"]
