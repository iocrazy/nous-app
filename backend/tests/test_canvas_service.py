"""Tests for the canvas service (Phase 1 Week 1).

The optimistic-lock branch is the load-bearing piece — these tests pin
it down with a fake repo so we don't need a real Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pytest

from app.schemas.canvas import CanvasCreate, CanvasUpdate
from app.services.canvas import CanvasConflict, CanvasService
from app.services.canvas.canvas_service import _canonical, _tokens_match

FROZEN_NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


class FakeCanvasRepository:
    """In-memory canvases store. Mimics the supabase-py repo's surface."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}
        self._next_id = 10000

    def _seed(self, **overrides: Any) -> Dict[str, Any]:
        self._next_id += 1
        row = {
            "id": str(self._next_id),
            "project_id": "5555",
            "name": "Untitled",
            "kind": "smart",
            "viewport_json": {"x": 0, "y": 0, "zoom": 1},
            "nodes_json": [],
            "connections_json": [],
            "node_ops_json": [],
            "connection_ops_json": [],
            "base_updated_at": FROZEN_NOW.isoformat(),
            "created_at": FROZEN_NOW.isoformat(),
            "updated_at": FROZEN_NOW.isoformat(),
            "created_by": None,
        }
        row.update(overrides)
        self.rows[str(row["id"])] = row
        return row

    async def get_by_id(self, canvas_id: str) -> Optional[Dict[str, Any]]:
        return self.rows.get(str(canvas_id))

    async def list_for_project(self, project_id: str):
        return [
            r for r in self.rows.values() if str(r["project_id"]) == str(project_id)
        ]

    async def get_project_id(self, canvas_id: str) -> Optional[str]:
        row = self.rows.get(str(canvas_id))
        return str(row["project_id"]) if row else None

    async def create(
        self,
        *,
        project_id: str,
        name: str,
        kind: str,
        created_by: Optional[str],
        viewport_json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._seed(
            project_id=project_id,
            name=name,
            kind=kind,
            created_by=created_by,
            viewport_json=viewport_json or {"x": 0, "y": 0, "zoom": 1},
        )

    async def update_with_lock(
        self,
        canvas_id: str,
        *,
        expected_base_updated_at: str,
        fields: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        row = self.rows.get(str(canvas_id))
        if row is None:
            return None
        if str(row["base_updated_at"]) != str(expected_base_updated_at):
            return None
        row.update(fields)
        new_ts = "2026-06-10T12:00:05+00:00"
        row["updated_at"] = new_ts
        row["base_updated_at"] = new_ts
        return row

    async def delete(self, canvas_id: str) -> bool:
        return self.rows.pop(str(canvas_id), None) is not None


# ============================================================
# _canonical / _tokens_match — timestamp tolerance
# ============================================================


class TestTokenComparison:
    def test_z_and_offset_are_equivalent(self):
        assert _tokens_match("2026-06-10T12:00:00Z", "2026-06-10T12:00:00+00:00")

    def test_microseconds_truncated(self):
        assert _tokens_match(
            "2026-06-10T12:00:00.123456+00:00",
            "2026-06-10T12:00:00+00:00",
        )

    def test_different_seconds_mismatch(self):
        assert not _tokens_match(
            "2026-06-10T12:00:00+00:00",
            "2026-06-10T12:00:01+00:00",
        )

    def test_empty_strings_match(self):
        assert _tokens_match("", "")

    def test_one_empty_mismatch(self):
        assert not _tokens_match("", "2026-06-10T12:00:00+00:00")

    def test_canonical_handles_no_fraction(self):
        assert _canonical("2026-06-10T12:00:00Z") == "2026-06-10T12:00:00+00:00"


# ============================================================
# CanvasService — happy paths
# ============================================================


@pytest.fixture
def svc():
    repo = FakeCanvasRepository()
    return CanvasService(repository=repo), repo


class TestCanvasReads:
    @pytest.mark.asyncio
    async def test_get_returns_seeded_row(self, svc):
        service, repo = svc
        row = repo._seed()
        got = await service.get(row["id"])
        assert got is not None
        assert got["id"] == row["id"]

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, svc):
        service, _ = svc
        assert await service.get("99999") is None

    @pytest.mark.asyncio
    async def test_list_filters_by_project(self, svc):
        service, repo = svc
        repo._seed(project_id="111")
        repo._seed(project_id="111")
        repo._seed(project_id="222")
        rows = await service.list_for_project("111")
        assert len(rows) == 2
        assert all(r["project_id"] == "111" for r in rows)


class TestCanvasCreate:
    @pytest.mark.asyncio
    async def test_create_defaults_kind_smart(self, svc):
        service, _ = svc
        data = CanvasCreate()
        row = await service.create_in_project("8888", data, created_by="u-1")
        assert row is not None
        assert row["kind"] == "smart"
        assert row["name"] == "Untitled"
        assert row["created_by"] == "u-1"

    @pytest.mark.asyncio
    async def test_create_classic_with_viewport(self, svc):
        service, _ = svc
        data = CanvasCreate(
            name="My Board",
            kind="classic",
            viewport_json={"x": 100, "y": 50, "zoom": 1.5},
        )
        row = await service.create_in_project("8888", data, created_by="u-2")
        assert row["kind"] == "classic"
        assert row["name"] == "My Board"
        assert row["viewport_json"]["zoom"] == 1.5


# ============================================================
# CanvasService — optimistic lock
# ============================================================


class TestOptimisticLock:
    @pytest.mark.asyncio
    async def test_update_with_matching_token_succeeds(self, svc):
        service, repo = svc
        row = repo._seed()
        original_token = str(row["base_updated_at"])
        update = CanvasUpdate(
            base_updated_at=datetime.fromisoformat(original_token),
            name="Renamed",
        )
        new_row = await service.update_with_lock(row["id"], update)
        assert new_row["name"] == "Renamed"
        # token rotated forward (FakeRepo bumps to FROZEN_NOW + 5s).
        assert new_row["base_updated_at"] != original_token

    @pytest.mark.asyncio
    async def test_update_with_stale_token_raises_conflict(self, svc):
        service, repo = svc
        row = repo._seed()
        stale_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        update = CanvasUpdate(base_updated_at=stale_ts, name="Loser")

        with pytest.raises(CanvasConflict) as exc_info:
            await service.update_with_lock(row["id"], update)

        # Conflict response carries the current server row so the
        # frontend can render the diff without another round-trip.
        current = exc_info.value.current
        assert current["id"] == row["id"]
        assert current["name"] == "Untitled"  # unchanged
        # The clobber attempt didn't land.
        assert repo.rows[row["id"]]["name"] == "Untitled"

    @pytest.mark.asyncio
    async def test_update_missing_canvas_raises_lookup(self, svc):
        service, _ = svc
        update = CanvasUpdate(base_updated_at=FROZEN_NOW)
        with pytest.raises(LookupError):
            await service.update_with_lock("99999", update)

    @pytest.mark.asyncio
    async def test_no_field_changes_with_valid_token_returns_row(self, svc):
        """PUT with only the lock token and no field changes is a no-op
        verify (lock-check-only)."""
        service, repo = svc
        row = repo._seed()
        update = CanvasUpdate(
            base_updated_at=datetime.fromisoformat(row["base_updated_at"]),
        )
        # No fields → repo's update_with_lock returns the row unchanged
        # (token still rotates because the fake repo always stamps now).
        result = await service.update_with_lock(row["id"], update)
        # The fields-empty path in our service still calls update_with_lock
        # which stamps a new token; that's fine.
        assert result["id"] == row["id"]

    @pytest.mark.asyncio
    async def test_token_with_microseconds_still_matches(self, svc):
        """A client that round-trips through a different JSON lib may
        keep microseconds while supabase strips them. The canonical
        comparison absorbs that."""
        service, repo = svc
        row = repo._seed(base_updated_at="2026-06-10T12:00:00+00:00")
        update = CanvasUpdate(
            base_updated_at=datetime(
                2026, 6, 10, 12, 0, 0, 123456, tzinfo=timezone.utc
            ),
            name="Microsecond",
        )
        result = await service.update_with_lock(row["id"], update)
        assert result["name"] == "Microsecond"


class TestCanvasDelete:
    @pytest.mark.asyncio
    async def test_delete_removes_row(self, svc):
        service, repo = svc
        row = repo._seed()
        assert await service.delete(row["id"]) is True
        assert row["id"] not in repo.rows

    @pytest.mark.asyncio
    async def test_delete_missing_returns_false(self, svc):
        service, _ = svc
        assert await service.delete("99999") is False
