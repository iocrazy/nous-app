import pytest

import app.services.topics.adapters.newsnow_adapter as newsnow_adapter_mod
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


@pytest.mark.asyncio
async def test_newsnow_default_url_reads_settings(monkeypatch):
    # No explicit api_url and no DB value -> resolution must fall back to
    # settings.NEWSNOW_API_URL (pydantic Settings), NOT os.getenv. The URL is
    # now resolved lazily (DB-first, see newsnow.api_url), not at construction.
    import app.services.topics.adapters.newsnow_adapter as mod

    async def fake_read_raw(key):
        return None

    monkeypatch.setattr(mod, "_read_raw", fake_read_raw)
    monkeypatch.setattr(mod.settings, "NEWSNOW_API_URL", "http://configured:4000/")
    adapter = NewsNowAdapter()
    assert await adapter._resolve_api_url() == "http://configured:4000"


@pytest.mark.asyncio
async def test_newsnow_raises_on_bad_status(monkeypatch):
    async def fake_get_json(url, timeout):
        return {"status": "error", "items": []}

    adapter = NewsNowAdapter(api_url="http://nn")
    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    with pytest.raises(ValueError):
        await adapter.fetch({"name": "X", "config": {"platform_id": "x"}})


@pytest.mark.asyncio
async def test_newsnow_fetch_uses_db_value_when_set(monkeypatch):
    # No explicit api_url override -> fetch() must resolve
    # system_settings.newsnow.api_url FIRST, ahead of the env fallback.
    async def fake_read_raw(key):
        assert key == "newsnow.api_url"
        return "http://db-configured:9000/"

    monkeypatch.setattr(newsnow_adapter_mod, "_read_raw", fake_read_raw)
    monkeypatch.setattr(
        newsnow_adapter_mod.settings, "NEWSNOW_API_URL", "http://env-fallback:4000"
    )

    async def fake_get_json(url, timeout):
        assert url.startswith("http://db-configured:9000/")
        return {"status": "success", "items": [{"title": "A", "url": "https://x"}]}

    adapter = NewsNowAdapter()
    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    out = await adapter.fetch({"name": "X", "config": {"platform_id": "hackernews"}})
    assert out[0].title == "A"


@pytest.mark.asyncio
async def test_newsnow_fetch_falls_back_to_env_when_db_absent(monkeypatch):
    async def fake_read_raw(key):
        return None  # absent — degrade to env fallback

    monkeypatch.setattr(newsnow_adapter_mod, "_read_raw", fake_read_raw)
    monkeypatch.setattr(
        newsnow_adapter_mod.settings, "NEWSNOW_API_URL", "http://env-fallback:4000"
    )

    async def fake_get_json(url, timeout):
        assert url.startswith("http://env-fallback:4000")
        return {"status": "success", "items": [{"title": "A", "url": "https://x"}]}

    adapter = NewsNowAdapter()
    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    await adapter.fetch({"name": "X", "config": {"platform_id": "hackernews"}})


@pytest.mark.asyncio
async def test_newsnow_fetch_falls_back_to_env_when_db_value_blank(monkeypatch):
    async def fake_read_raw(key):
        return "   "  # blank string — treated as unset

    monkeypatch.setattr(newsnow_adapter_mod, "_read_raw", fake_read_raw)
    monkeypatch.setattr(
        newsnow_adapter_mod.settings, "NEWSNOW_API_URL", "http://env-fallback:4000"
    )

    async def fake_get_json(url, timeout):
        assert url.startswith("http://env-fallback:4000")
        return {"status": "success", "items": [{"title": "A", "url": "https://x"}]}

    adapter = NewsNowAdapter()
    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    await adapter.fetch({"name": "X", "config": {"platform_id": "hackernews"}})


@pytest.mark.asyncio
async def test_newsnow_fetch_degrade_safe_on_reader_exception(monkeypatch):
    async def fake_read_raw(key):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(newsnow_adapter_mod, "_read_raw", fake_read_raw)
    monkeypatch.setattr(
        newsnow_adapter_mod.settings, "NEWSNOW_API_URL", "http://env-fallback:4000"
    )

    async def fake_get_json(url, timeout):
        assert url.startswith("http://env-fallback:4000")
        return {"status": "success", "items": [{"title": "A", "url": "https://x"}]}

    adapter = NewsNowAdapter()
    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    # Must not raise — degrades to env fallback even when the reader blows up.
    out = await adapter.fetch({"name": "X", "config": {"platform_id": "hackernews"}})
    assert out[0].title == "A"


@pytest.mark.asyncio
async def test_newsnow_explicit_api_url_skips_db_read(monkeypatch):
    # Explicit constructor override is a deliberate pin — must win over the
    # DB value and must not even trigger a DB read.
    async def fake_read_raw(key):
        raise AssertionError("must not read DB when api_url explicitly given")

    monkeypatch.setattr(newsnow_adapter_mod, "_read_raw", fake_read_raw)

    async def fake_get_json(url, timeout):
        assert url.startswith("http://explicit")
        return {"status": "success", "items": [{"title": "A", "url": "https://x"}]}

    adapter = NewsNowAdapter(api_url="http://explicit")
    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    await adapter.fetch({"name": "X", "config": {"platform_id": "hackernews"}})
