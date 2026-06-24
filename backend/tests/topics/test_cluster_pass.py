import pytest

from app.workflows.topic_inspiration import cluster_unassigned_once


class _FakeRepo:
    """Records calls; `nearest` controls the match decision per hotspot id."""

    def __init__(self, rows, nearest_by_id=None):
        self.rows = rows
        self.nearest_by_id = nearest_by_id or {}
        self.assigned: list[tuple] = []
        self.recomputed: list[str] = []
        self.created: list[str] = []
        self._next_gid = 1000
        self._current = None

    async def list_unclustered(self, *, window_hours=48, limit=60):
        return self.rows

    async def nearest_group(self, vec, *, window_hours=48):
        # vec carries the hotspot id in tests (e.g. "vec-1")
        self._current = vec
        return self.nearest_by_id.get(vec)

    async def assign_hotspot(self, hotspot_id, group_id):
        self.assigned.append((hotspot_id, group_id))

    async def recompute_group(self, group_id):
        self.recomputed.append(group_id)

    async def create_group(self, *, label, vec):
        self._next_gid += 1
        gid = str(self._next_gid)
        self.created.append(gid)
        return gid


@pytest.mark.asyncio
async def test_cluster_assigns_to_matching_group():
    rows = [{"id": "1", "title": "GPT-5", "source_id": "s1", "vec": "vec-1"}]
    repo = _FakeRepo(rows, nearest_by_id={"vec-1": {"id": "777", "sim": 0.92}})
    out = await cluster_unassigned_once(repo=repo)
    assert out == {"processed": 1, "clustered": 1, "new_groups": 0}
    assert repo.assigned == [("1", "777")]
    assert repo.recomputed == ["777"]
    assert repo.created == []


@pytest.mark.asyncio
async def test_cluster_creates_new_group_when_below_threshold():
    rows = [{"id": "1", "title": "GPT-5", "source_id": "s1", "vec": "vec-1"}]
    # a candidate exists but similarity is under threshold -> new group
    repo = _FakeRepo(rows, nearest_by_id={"vec-1": {"id": "777", "sim": 0.4}})
    out = await cluster_unassigned_once(repo=repo)
    assert out == {"processed": 1, "clustered": 0, "new_groups": 1}
    assert repo.created and repo.assigned == [("1", repo.created[0])]
    assert repo.recomputed == []


@pytest.mark.asyncio
async def test_cluster_creates_new_group_when_no_candidate():
    rows = [{"id": "1", "title": "T", "source_id": "s1", "vec": "vec-1"}]
    repo = _FakeRepo(rows, nearest_by_id={})  # no active group
    out = await cluster_unassigned_once(repo=repo)
    assert out["new_groups"] == 1 and out["clustered"] == 0


@pytest.mark.asyncio
async def test_cluster_skips_rows_without_vec():
    rows = [{"id": "1", "title": "T", "source_id": "s1", "vec": None}]
    repo = _FakeRepo(rows)
    out = await cluster_unassigned_once(repo=repo)
    assert out == {"processed": 1, "clustered": 0, "new_groups": 0}
    assert repo.created == [] and repo.assigned == []


@pytest.mark.asyncio
async def test_cluster_empty_is_noop():
    repo = _FakeRepo([])
    out = await cluster_unassigned_once(repo=repo)
    assert out == {"processed": 0, "clustered": 0, "new_groups": 0}
