"""``merge_slide_prompt`` persists the slide text and its origin stamp together.

mig 455's rule is that the origin follows the LAST writer of the positive
text. ``caption_slide`` used to write the two in separate calls, so a failure
between them left the text with a stale origin and nothing said so. The
``extra`` parameter exists to close that window: same ORM object, same flush.

It is deliberately a one-key whitelist. A general "extra columns" bag on a
per-slide merge would become a second, undocumented way to PATCH a resource
row — the exact whole-row write this method exists to avoid.
"""

from contextlib import asynccontextmanager

import pytest

from app.models import Resources
from app.repositories import resources_repository as repo_mod
from app.repositories.resources_repository import ResourcesRepository


class _CapSession:
    """Minimal fake write session: queued ``get`` result, counted ``flush``."""

    def __init__(self, get_obj):
        self._get_obj = get_obj
        self.gets = []
        self.flushed = 0

    async def get(self, model, pk):
        self.gets.append((model, pk))
        return self._get_obj

    async def flush(self):
        self.flushed += 1


@asynccontextmanager
async def _fake_write(session):
    yield session


def _row(**kw) -> Resources:
    return Resources(
        id=101,
        creator_id="11111111-2222-3333-4444-555555555555",
        source_type="web",
        filename="album",
        **kw,
    )


@pytest.mark.asyncio
async def test_extra_lands_in_the_same_flush_as_the_slide_text(monkeypatch):
    obj = _row(slide_prompts={"001.jpg": {"en": "first"}}, prompt_origin=None)
    session = _CapSession(get_obj=obj)
    monkeypatch.setattr(repo_mod, "write_scope", lambda: _fake_write(session))

    out = await ResourcesRepository().merge_slide_prompt(
        "101", "002.jpg", {"en": "second"}, extra={"prompt_origin": "captioned"}
    )

    # ONE flush carries both — no window in which the text is saved with a
    # stale origin.
    assert session.flushed == 1
    assert obj.slide_prompts == {
        "001.jpg": {"en": "first"},
        "002.jpg": {"en": "second"},
    }
    assert obj.prompt_origin == "captioned"
    assert out == {"en": "second"}


@pytest.mark.asyncio
async def test_extra_is_optional_and_leaves_the_origin_alone(monkeypatch):
    obj = _row(slide_prompts=None, prompt_origin="typed")
    session = _CapSession(get_obj=obj)
    monkeypatch.setattr(repo_mod, "write_scope", lambda: _fake_write(session))

    await ResourcesRepository().merge_slide_prompt("101", "001.jpg", {"en": "x"})

    assert session.flushed == 1
    assert obj.slide_prompts == {"001.jpg": {"en": "x"}}
    assert obj.prompt_origin == "typed"


@pytest.mark.asyncio
async def test_unknown_extra_key_raises_before_anything_is_written(monkeypatch):
    obj = _row(slide_prompts=None, prompt_origin=None)
    session = _CapSession(get_obj=obj)
    monkeypatch.setattr(repo_mod, "write_scope", lambda: _fake_write(session))

    with pytest.raises(ValueError, match="is_trashed"):
        await ResourcesRepository().merge_slide_prompt(
            "101", "001.jpg", {"en": "x"}, extra={"is_trashed": True}
        )

    # Rejected at the door: no session opened, no partial write.
    assert session.gets == []
    assert session.flushed == 0
    assert obj.slide_prompts is None
