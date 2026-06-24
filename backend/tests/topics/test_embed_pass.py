import pytest

from app.workflows.topic_inspiration import _embed_text, embed_unembedded_once


def test_embed_text_combines_title_and_summary():
    assert _embed_text({"title": "T", "ai_summary": "S"}) == "T\nS"
    # falls back to content_original when no ai_summary
    assert _embed_text({"title": "T", "content_original": "C"}) == "T\nC"
    # title only
    assert _embed_text({"title": "T"}) == "T"


class _FakeRepo:
    def __init__(self, rows):
        self.rows = rows
        self.patched: list[tuple[str, list]] = []

    async def list_unembedded(self, limit=40):
        return self.rows

    async def patch_embedding(self, hotspot_id, embedding):
        self.patched.append((hotspot_id, embedding))


class _FakeEmbedder:
    def __init__(self, vec):
        self.vec = vec
        self.calls = 0

    async def embed_text(self, text):
        self.calls += 1
        return self.vec


@pytest.mark.asyncio
async def test_embed_pass_embeds_and_patches():
    repo = _FakeRepo([{"id": "1", "title": "A"}, {"id": "2", "title": "B"}])
    embedder = _FakeEmbedder([0.1, 0.2])
    out = await embed_unembedded_once(hotspots_repo=repo, embedder=embedder)
    assert out == {"unembedded": 2, "embedded": 2}
    assert [p[0] for p in repo.patched] == ["1", "2"]


@pytest.mark.asyncio
async def test_embed_pass_skips_when_embedder_returns_none():
    repo = _FakeRepo([{"id": "1", "title": "A"}])
    embedder = _FakeEmbedder(None)  # provider unconfigured/failed
    out = await embed_unembedded_once(hotspots_repo=repo, embedder=embedder)
    assert out == {"unembedded": 1, "embedded": 0}
    assert repo.patched == []


@pytest.mark.asyncio
async def test_embed_pass_empty_is_noop():
    repo = _FakeRepo([])
    out = await embed_unembedded_once(hotspots_repo=repo, embedder=_FakeEmbedder([1.0]))
    assert out == {"unembedded": 0, "embedded": 0}
