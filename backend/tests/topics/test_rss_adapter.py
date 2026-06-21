from datetime import datetime, timezone

import pytest

from app.services.topics.adapters.rss_adapter import RssAdapter

SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Demo</title>
  <item><title>First Post</title><link>https://demo.com/1</link>
    <description>Body one</description>
    <pubDate>Mon, 16 Jun 2026 06:00:00 GMT</pubDate></item>
  <item><title>Second Post</title><link>https://demo.com/2</link>
    <description>Body two</description></item>
</channel></rss>"""


@pytest.mark.asyncio
async def test_rss_adapter_parses_items(monkeypatch):
    async def fake_get(url, timeout):
        return SAMPLE_RSS

    adapter = RssAdapter()
    monkeypatch.setattr(adapter, "_get_text", fake_get)
    out = await adapter.fetch(
        {"name": "Demo", "config": {"url": "https://demo.com/feed"}}
    )
    assert len(out) == 2
    assert out[0].title == "First Post"
    assert out[0].url == "https://demo.com/1"
    assert out[0].source_label == "Demo (RSS)"
    assert out[0].content == "Body one"
    assert out[0].captured_at == datetime(2026, 6, 16, 6, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_rss_adapter_raises_on_empty(monkeypatch):
    async def fake_get(url, timeout):
        return "<rss><channel></channel></rss>"

    adapter = RssAdapter()
    monkeypatch.setattr(adapter, "_get_text", fake_get)
    with pytest.raises(ValueError):
        await adapter.fetch({"name": "Empty", "config": {"url": "x"}})
