"""桩：登记口的两个外部面——repository 与事件写入。

真库不在单测里跑（repository 的 ORM 语句由 schema-drift 的集成套件执行），
这里只断言「我们发出去的东西」：插了什么行、落了什么事件。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Optional

import pytest


class RepoSpy:
    """站在 ``RunDeliverablesRepository`` 的位置上。

    ``latest_version_returns`` 是下一次读到的版本号；给一个列表就按顺序吐，
    并发用例靠它模拟「第一次读到 None，第二次读到别人刚提交的 1」。
    """

    def __init__(self) -> None:
        self.inserts: list[dict[str, Any]] = []
        self.latest_calls: list[tuple[str, str]] = []
        self.latest_version_returns: Any = None
        self.raise_integrity_on: set[int] = set()
        #: ``(row_id, seq)`` per ``set_seq`` — the seq back-fill that can only
        #: happen after the event exists (the row is inserted first).
        self.seq_calls: list[tuple[str, int]] = []
        self.raise_on_set_seq = False
        self._ids = itertools.count(1000)

    async def latest_version(self, *, kind: str, ref_id: str) -> Optional[int]:
        self.latest_calls.append((kind, ref_id))
        returns = self.latest_version_returns
        if isinstance(returns, list):
            idx = len(self.latest_calls) - 1
            return returns[idx] if idx < len(returns) else returns[-1]
        return returns

    async def insert_version(self, **values: Any) -> dict[str, Any]:
        from sqlalchemy.exc import IntegrityError

        self.inserts.append(dict(values))
        if len(self.inserts) in self.raise_integrity_on:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))
        return {"id": next(self._ids), **values}

    async def set_seq(self, *, row_id: Any, seq: int) -> None:
        self.seq_calls.append((str(row_id), seq))
        if self.raise_on_set_seq:
            raise RuntimeError("update failed")


@dataclass
class SpiedEvent:
    type: str
    payload: dict[str, Any]
    turn: Optional[int]
    step: Optional[int]


class EmitSpy:
    """A recorder in the ``events.emit`` sense: it has ``record_event``."""

    def __init__(self) -> None:
        self.events: list[SpiedEvent] = []
        #: The seq the last recorded event took — both real recorders expose
        #: it (``RunRecorder.last_event_seq`` / ``_LateRecorder``), and the
        #: registry reads it to stamp ``run_deliverables.seq``. ``None`` until
        #: something is recorded, exactly like the real ones.
        self.last_event_seq: Optional[int] = None

    async def record_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        turn: Optional[int] = None,
        step: Optional[int] = None,
    ) -> None:
        self.events.append(SpiedEvent(event_type, payload, turn, step))
        self.last_event_seq = len(self.events)


@pytest.fixture
def repo_spy(monkeypatch) -> RepoSpy:
    from app.services.deliverables import registry

    spy = RepoSpy()
    monkeypatch.setattr(registry, "RunDeliverablesRepository", lambda: spy)
    return spy


@pytest.fixture
def emit_spy(monkeypatch) -> EmitSpy:
    """Also stands in for ``_writer_for`` so no test reaches the database."""
    from app.services.deliverables import registry

    spy = EmitSpy()

    async def _writer_for(_run_id: Any) -> EmitSpy:
        return spy

    monkeypatch.setattr(registry, "_writer_for", _writer_for)
    return spy
