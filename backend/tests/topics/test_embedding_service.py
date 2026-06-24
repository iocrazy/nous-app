import pytest

from app.services.topics.embedding_service import TopicEmbeddingService


def test_parse_vector_dict_shape():
    body = {"data": {"embedding": [0.1, 0.2, 0.3], "object": "embedding"}}
    assert TopicEmbeddingService._parse_vector(body) == [0.1, 0.2, 0.3]


def test_parse_vector_list_shape():
    body = {"data": [{"embedding": [1, 2]}]}
    assert TopicEmbeddingService._parse_vector(body) == [1.0, 2.0]


def test_parse_vector_malformed_returns_none():
    assert TopicEmbeddingService._parse_vector({}) is None
    assert TopicEmbeddingService._parse_vector({"data": {}}) is None
    assert TopicEmbeddingService._parse_vector({"data": {"embedding": []}}) is None
    assert TopicEmbeddingService._parse_vector({"data": {"embedding": "x"}}) is None


class _Gov:
    def __init__(self, base_url, model, api_key):
        self.base_url, self.model, self.api_key = base_url, model, api_key
        self.api_key_present = bool(api_key)


def _patch_gov(monkeypatch, gov):
    async def _fake(module):
        assert module == "embedding"
        return gov

    monkeypatch.setattr(
        "app.services.topics.embedding_service.get_module_governance", _fake
    )


@pytest.mark.asyncio
async def test_embed_text_skips_when_unconfigured(monkeypatch):
    svc = TopicEmbeddingService()
    _patch_gov(monkeypatch, _Gov("", "", ""))  # no provider
    assert await svc.embed_text("hello") is None


@pytest.mark.asyncio
async def test_embed_text_empty_input_returns_none():
    assert await TopicEmbeddingService().embed_text("   ") is None


@pytest.mark.asyncio
async def test_embed_text_posts_and_parses(monkeypatch):
    svc = TopicEmbeddingService()
    _patch_gov(monkeypatch, _Gov("http://ark/x/embeddings/multimodal", "m", "k"))
    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"embedding": [0.5, 0.6]}}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            seen["url"] = url
            seen["json"] = json
            seen["auth"] = headers.get("Authorization")
            return _Resp()

    monkeypatch.setattr(
        "app.services.topics.embedding_service.httpx.AsyncClient", _Client
    )
    out = await svc.embed_text("hello world")
    assert out == [0.5, 0.6]
    assert seen["url"] == "http://ark/x/embeddings/multimodal"
    assert seen["json"]["model"] == "m"
    assert seen["json"]["input"] == [{"type": "text", "text": "hello world"}]
    assert seen["auth"] == "Bearer k"
