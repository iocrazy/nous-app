"""Sprint 8.5 — link extraction + injection rendering."""
from __future__ import annotations

import pytest

from app.boundary.external_text import HARD_LIMIT_CHARS, neutralize_external_text
from app.services import link_injection as li
from app.services.link_understanding import LinkSummary, LinkUnderstandingError


# ─── extract_urls ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_extract_urls_basic():
    urls = li.extract_urls("see https://example.com/x and http://other.com")
    assert urls == ["https://example.com/x", "http://other.com"]


@pytest.mark.unit
def test_extract_urls_strips_trailing_punctuation():
    """'see https://x.com/foo.' → 'https://x.com/foo' (period belongs to sentence)."""
    urls = li.extract_urls("see https://x.com/foo. And: https://y.com),")
    assert urls == ["https://x.com/foo", "https://y.com"]


@pytest.mark.unit
def test_extract_urls_dedupes():
    urls = li.extract_urls("https://x.com/a and again https://x.com/a")
    assert urls == ["https://x.com/a"]


@pytest.mark.unit
def test_extract_urls_caps_count():
    text = " ".join(f"https://x.com/{i}" for i in range(10))
    urls = li.extract_urls(text, max_urls=3)
    assert len(urls) == 3


@pytest.mark.unit
def test_extract_urls_ignores_non_http_schemes():
    """ftp://, file://, javascript:, mailto: etc. all out of scope."""
    text = "ftp://x.com mailto:a@b.com javascript:alert(1)"
    assert li.extract_urls(text) == []


@pytest.mark.unit
def test_extract_urls_empty():
    assert li.extract_urls("") == []
    assert li.extract_urls(None or "") == []


@pytest.mark.unit
def test_extract_from_markdown_link():
    """[anchor](url) should still extract the URL."""
    urls = li.extract_urls("see [the spec](https://spec.example.com/v1) for more")
    assert urls == ["https://spec.example.com/v1"]


# ─── extract_urls_from_messages ───────────────────────────────────────


@pytest.mark.unit
def test_extract_only_last_user_msg_by_default():
    msgs = [
        {"role": "user", "content": "first https://old.com"},
        {"role": "assistant", "content": "ack"},
        {"role": "user", "content": "now https://new.com"},
    ]
    urls = li.extract_urls_from_messages(msgs)
    assert urls == ["https://new.com"]


@pytest.mark.unit
def test_extract_skips_assistant_messages_by_default():
    """Don't fetch URLs the assistant emitted — could be hallucinated /
    untrusted-loop attack vector."""
    msgs = [
        {"role": "user", "content": "what about https://safe.com"},
        {"role": "assistant", "content": "also see https://attacker.com"},
    ]
    urls = li.extract_urls_from_messages(msgs, only_last=False)
    assert urls == ["https://safe.com"]


@pytest.mark.unit
def test_extract_can_scan_full_history():
    msgs = [
        {"role": "user", "content": "first https://a.com"},
        {"role": "user", "content": "second https://b.com"},
    ]
    urls = li.extract_urls_from_messages(msgs, only_last=False)
    assert urls == ["https://a.com", "https://b.com"]


@pytest.mark.unit
def test_extract_dedupes_across_messages():
    msgs = [
        {"role": "user", "content": "https://x.com"},
        {"role": "user", "content": "again https://x.com"},
    ]
    urls = li.extract_urls_from_messages(msgs, only_last=False)
    assert urls == ["https://x.com"]


# ─── render_block / render_failure_block ──────────────────────────────


def _make_summary(url="https://x.com/", body="hello world body") -> LinkSummary:
    neutralized = neutralize_external_text(body, max_chars=200)
    return LinkSummary(
        url=url,
        status_code=200,
        content_type="text/html",
        title="Page Title",
        description="A description",
        body_text=body,
        neutralized=neutralized,
    )


@pytest.mark.unit
def test_render_block_includes_metadata_and_neutralized_body():
    block = li.render_block(_make_summary())
    assert "https://x.com/" in block
    assert "Page Title" in block
    assert "A description" in block
    assert "EXTERNAL_CONTENT_" in block  # neutralizer wrap present


@pytest.mark.unit
def test_render_block_handles_missing_metadata():
    summary = LinkSummary(
        url="https://x.com/",
        status_code=200,
        content_type="text/html",
        title=None,
        description=None,
        neutralized=None,
    )
    block = li.render_block(summary)
    assert "(no title)" in block
    assert "(none)" in block
    assert "(no body extracted)" in block


@pytest.mark.unit
def test_render_failure_block_truncates_long_reason():
    long = "x" * 5000
    block = li.render_failure_block("https://x.com/", long)
    assert len(block) < 600  # well under prompt-pollution risk
    assert "https://x.com/" in block
    assert "error=true" in block


@pytest.mark.unit
def test_render_failure_block_strips_multiline_reason():
    """Multi-line errors with stack traces could leak internals."""
    reason = "first line\ninternal_path=/etc/secret\nstack..."
    block = li.render_failure_block("https://x.com/", reason)
    assert "internal_path" not in block
    assert "first line" in block


# ─── fetch_and_render ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_and_render_empty_input(monkeypatch):
    result = await li.fetch_and_render([])
    assert result.urls == []
    assert result.blocks == []
    assert result.has_content is False
    assert result.joined == ""


@pytest.mark.asyncio
async def test_fetch_and_render_success(monkeypatch):
    """Stub understand_link → success block lands in result."""
    summary = _make_summary("https://x.com/")

    async def _stub(url, **_kw):
        return summary

    monkeypatch.setattr(li, "understand_link", _stub)
    result = await li.fetch_and_render(["https://x.com/"])
    assert len(result.blocks) == 1
    assert "Page Title" in result.blocks[0]
    assert result.failures == []
    assert result.has_content is True


@pytest.mark.asyncio
async def test_fetch_and_render_failure_keeps_placeholder(monkeypatch):
    """A failed fetch produces a placeholder block — agent still sees
    'we tried this URL' rather than nothing."""
    async def _stub(url, **_kw):
        raise LinkUnderstandingError("upstream returned HTTP 404")

    monkeypatch.setattr(li, "understand_link", _stub)
    result = await li.fetch_and_render(["https://x.com/"])
    assert len(result.blocks) == 1
    assert "error=true" in result.blocks[0]
    assert len(result.failures) == 1
    assert result.failures[0][0] == "https://x.com/"


@pytest.mark.asyncio
async def test_fetch_and_render_mixed_outcomes(monkeypatch):
    """Concurrent fetches: some succeed, some fail, all get blocks."""
    summary = _make_summary("https://ok.com/")

    async def _stub(url, **_kw):
        if "ok" in url:
            return summary
        raise LinkUnderstandingError("nope")

    monkeypatch.setattr(li, "understand_link", _stub)
    result = await li.fetch_and_render(["https://ok.com/", "https://bad.com/"])
    assert len(result.blocks) == 2
    assert len(result.failures) == 1
    assert result.failures[0][0] == "https://bad.com/"


@pytest.mark.asyncio
async def test_fetch_and_render_unexpected_exception_caught(monkeypatch):
    """Non-LinkUnderstandingError shouldn't crash the batch."""
    async def _stub(url, **_kw):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(li, "understand_link", _stub)
    result = await li.fetch_and_render(["https://x.com/"])
    assert len(result.failures) == 1
    assert "RuntimeError" in result.failures[0][1]


@pytest.mark.unit
def test_block_total_under_neutralizer_hard_limit():
    """Sanity: a typical full block fits inside the boundary's hard
    limit so injecting it back through neutralize_external_text won't
    blow up. Catches future template tweaks that bloat the block."""
    block = li.render_block(_make_summary())
    assert len(block) < HARD_LIMIT_CHARS
