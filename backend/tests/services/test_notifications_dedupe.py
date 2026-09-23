"""``notify()`` dedupe window vs. producers whose events are distinct per task.

The 10-minute window keys on (user, kind, link_kind, link_id). Agent video
results carry no per-task link (a conversation has no link kind; two videos
on one issue share the issue link), so under that key a second video finishing
inside the window was silently dropped. ``dedupe=False`` lets such a producer
opt out; every other producer keeps the window exactly as before.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.services import notifications as n

pytestmark = pytest.mark.unit

_USER = "11111111-1111-1111-1111-111111111111"


class _Inbox:
    """In-memory ``inbox_notifications``: the fake window matches the real
    key, and every row is inside the window."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def is_duplicate(self, user_id, kind, link_kind, link_id) -> bool:
        return any(
            (r["user_id"], r["kind"], r["link_kind"], r["link_id"])
            == (user_id, kind, link_kind, link_id)
            for r in self.rows
        )

    @asynccontextmanager
    async def write_scope(self):
        inbox = self

        class _Result:
            def __init__(self, new_id: int) -> None:
                self._id = new_id

            def scalar(self) -> int:
                return self._id

        class _Session:
            async def execute(self, stmt):
                inbox.rows.append(dict(stmt.compile().params))
                return _Result(len(inbox.rows))

        yield _Session()


@pytest.fixture
def inbox(monkeypatch) -> _Inbox:
    box = _Inbox()
    monkeypatch.setattr(n, "_is_duplicate", box.is_duplicate)
    monkeypatch.setattr(n, "write_scope", box.write_scope)
    return box


@pytest.mark.asyncio
async def test_default_producers_still_dedupe_inside_the_window(inbox):
    for _ in range(2):
        await n.notify(
            _USER,
            "generation_result",
            "Download complete",
            link_kind="resource",
            link_id="42",
        )
    assert len(inbox.rows) == 1


@pytest.mark.asyncio
async def test_dedupe_false_inserts_every_event(inbox):
    for _ in range(2):
        await n.notify(_USER, "generation_result", "Video ready", dedupe=False)
    assert len(inbox.rows) == 2


@pytest.mark.asyncio
async def test_two_agent_videos_in_one_conversation_each_notify(inbox):
    from app.workflows import agent_video as m

    for status in ("completed", "failed"):
        for _ in range(2):
            await m.notify_media_result_step(
                user_id=_USER,
                status=status,
                target_kind="conversation",
                target_id=9001,
                error_code=None if status == "completed" else "daemon_offline",
            )
    assert len(inbox.rows) == 4


@pytest.mark.asyncio
async def test_two_agent_videos_on_one_issue_each_notify(inbox, monkeypatch):
    from app.workflows import agent_video as m

    async def _ident(issue_id: int) -> str:
        return f"MH-{issue_id}"

    monkeypatch.setattr(m, "_issue_link_id", _ident)
    for _ in range(2):
        await m.notify_media_result_step(
            user_id=_USER,
            status="completed",
            target_kind="issue",
            target_id=77,
            error_code=None,
        )
    assert len(inbox.rows) == 2
    assert {(r["link_kind"], r["link_id"]) for r in inbox.rows} == {("issue", "MH-77")}
