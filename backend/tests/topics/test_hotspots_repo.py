from app.repositories.hotspots_repository import HotspotsRepository
from app.services.topics.adapters.base import HotspotCandidate


def test_build_rows_sets_dedup_and_global_user():
    repo = HotspotsRepository()
    cands = [HotspotCandidate(title="A", url="https://x.com/a", source_label="S")]
    rows = repo.build_rows(cands, source_id="42", category="model")
    assert len(rows) == 1
    r = rows[0]
    assert r["user_id"] is None  # Phase 1 global
    assert r["source_id"] == "42"
    assert r["title"] == "A"
    assert r["category"] == "model"
    assert r["dedup_key"]
    assert r["media_url"] is None


def test_build_rows_carries_media_url():
    repo = HotspotsRepository()
    cands = [HotspotCandidate(title="V", url="u", media_url="https://m/v.mp4")]
    rows = repo.build_rows(cands, source_id="1", category=None)
    assert rows[0]["media_url"] == "https://m/v.mp4"
