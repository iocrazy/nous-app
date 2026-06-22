import pytest

from app.services.topics.adapters.newsnow_adapter import NewsNowAdapter


@pytest.mark.asyncio
async def test_newsnow_maps_items(monkeypatch):
    async def fake_get_json(url, timeout):
        assert "id=hackernews" in url
        return {
            "status": "success",
            "items": [
                {"id": "1", "title": "Item A", "url": "https://h.com/a"},
                {"id": "2", "title": "Item B", "url": "https://h.com/b"},
            ],
        }

    adapter = NewsNowAdapter(api_url="http://nn")
    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    out = await adapter.fetch({"name": "HN", "config": {"platform_id": "hackernews"}})
    assert [c.title for c in out] == ["Item A", "Item B"]
    assert out[0].source_label == "HN"
    # list position becomes 1-based board rank for heat
    assert [c.rank for c in out] == [1, 2]


def test_newsnow_default_url_reads_settings(monkeypatch):
    # No explicit api_url -> must read settings.NEWSNOW_API_URL (pydantic
    # Settings from bind-mounted /app/.env), NOT os.getenv.
    import app.services.topics.adapters.newsnow_adapter as mod

    monkeypatch.setattr(mod.settings, "NEWSNOW_API_URL", "http://configured:4000/")
    adapter = NewsNowAdapter()
    assert adapter.api_url == "http://configured:4000"  # trailing slash stripped


@pytest.mark.asyncio
async def test_newsnow_raises_on_bad_status(monkeypatch):
    async def fake_get_json(url, timeout):
        return {"status": "error", "items": []}

    adapter = NewsNowAdapter(api_url="http://nn")
    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    with pytest.raises(ValueError):
        await adapter.fetch({"name": "X", "config": {"platform_id": "x"}})
