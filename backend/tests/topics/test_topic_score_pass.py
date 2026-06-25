import pytest

from app.workflows.topic_inspiration import score_unscored_once


class _FakeHotspots:
    def __init__(self, rows):
        self.rows = rows
        self.patched = []

    async def list_unscored(self, limit=60):
        return self.rows[:limit]

    async def patch_enrichment(self, hotspot_id, enrichment):
        self.patched.append((hotspot_id, enrichment))


class _FakeSources:
    async def tier_map(self):
        return {}  # all default tier 2


class _FakeScorer:
    def __init__(self):
        self.calls = 0

    async def score_items(self, items, user_id=None):
        self.calls += 1
        # Score even indices only, to exercise partial enrichment. Emits raw
        # dims (no composite — code computes that from dims + source tier).
        return {
            it["i"]: {
                "dims": {"impact": 0.7, "novelty": 0.6},
                "confidence": 0.8,
                "reason": "r",
                "ai_summary": "s",
                "category": "model",
                "tags": ["t"],
            }
            for it in items
            if it["i"] % 2 == 0
        }


@pytest.mark.asyncio
async def test_score_unscored_batches_and_patches():
    rows = [
        {
            "id": str(n),
            "source_id": "10",
            "source_label": "S",
            "title": f"t{n}",
            "content_original": "c",
        }
        for n in range(5)
    ]
    hs = _FakeHotspots(rows)
    sc = _FakeScorer()
    out = await score_unscored_once(
        hotspots_repo=hs,
        scorer=sc,
        sources_repo=_FakeSources(),
        max_items=10,
        batch_size=2,
    )
    # 5 rows, batch_size 2 -> 3 batches
    assert sc.calls == 3
    assert out["unscored"] == 5
    # within each batch, only even local indices (0) get scored -> 3 patched
    assert out["scored"] == len(hs.patched) == 3
    # patched ids are the first of each batch (local idx 0): rows 0, 2, 4
    assert {pid for pid, _ in hs.patched} == {"0", "2", "4"}
    # code computed the composite score + persisted the raw dims
    _, patch = hs.patched[0]
    assert isinstance(patch["score"], float) and 0.0 <= patch["score"] <= 1.0
    assert patch["score_dims"] == {"impact": 0.7, "novelty": 0.6}


@pytest.mark.asyncio
async def test_score_unscored_empty():
    hs = _FakeHotspots([])
    out = await score_unscored_once(hotspots_repo=hs, scorer=_FakeScorer())
    assert out == {"unscored": 0, "scored": 0}


@pytest.mark.asyncio
async def test_score_unscored_batch_failure_isolated():
    rows = [
        {
            "id": str(n),
            "source_id": "10",
            "source_label": "S",
            "title": "t",
            "content_original": "c",
        }
        for n in range(3)
    ]
    hs = _FakeHotspots(rows)

    class _Boom:
        async def score_items(self, items, user_id=None):
            raise RuntimeError("llm down")

    out = await score_unscored_once(
        hotspots_repo=hs,
        scorer=_Boom(),
        sources_repo=_FakeSources(),
        max_items=10,
        batch_size=2,
    )
    # both batches raise -> isolated, 0 scored, no crash
    assert out["scored"] == 0 and out["unscored"] == 3
