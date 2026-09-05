"""Phase-4 spine, primitive ①: there is exactly one way an event reaches the
transcript. Every emitter in app/services/ai goes through
``runner/events.py::emit`` → ``RunRecorder.record_event`` →
``RunEventWriter.append``. A fourth ``record_event(`` call site (or a fresh
``_emit``-style helper) is what this test exists to refuse — phase 2 grew
three of them before this seam was cut.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.ai.runner import events

APP_AI = Path(__file__).resolve().parents[2] / "app" / "services" / "ai"
ALLOWED = {
    APP_AI / "runner" / "events.py",  # the entry
    APP_AI / "runner" / "run_recorder.py",  # the definition
}


def _py_files():
    return sorted(p for p in APP_AI.rglob("*.py") if p.is_file())


@pytest.mark.unit
def test_no_record_event_call_outside_the_single_entry():
    offenders = []
    for path in _py_files():
        if path in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\brecord_event\(", line) and not line.lstrip().startswith(
                "#"
            ):
                offenders.append(f"{path.relative_to(APP_AI)}:{lineno}: {line.strip()}")
    assert offenders == [], "route these through runner.events.emit:\n" + "\n".join(
        offenders
    )


@pytest.mark.unit
def test_no_second_best_effort_emit_helper_is_defined():
    """``_emit`` / ``emit_*`` definitions outside events.py must delegate, so
    their bodies contain ``emit(`` and no direct ``record_event``."""
    offenders = []
    for path in _py_files():
        if path == APP_AI / "runner" / "events.py":
            continue
        src = path.read_text()
        for m in re.finditer(
            r"^async def (_emit|emit_\w+)\(.*?(?=^(?:async )?def |\Z)", src, re.S | re.M
        ):
            body = m.group(0)
            if "emit(" not in body:
                offenders.append(f"{path.relative_to(APP_AI)}::{m.group(1)}")
    assert offenders == [], offenders


class _Rec:
    def __init__(self, *, fail=False, legacy=False):
        self.events = []
        self.fail = fail
        self.legacy = legacy

    if True:

        async def record_event(self, event_type, payload, *, turn=None, step=None):
            if self.fail:
                raise RuntimeError("db down")
            self.events.append((event_type, payload, turn, step))


class _LegacyRec:
    """Two-positional signature (pre-mig-453 stand-ins in tests)."""

    def __init__(self):
        self.events = []

    async def record_event(self, event_type, payload):
        self.events.append((event_type, payload))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_passes_coordinates_and_reports_success():
    rec = _Rec()
    assert await events.emit(rec, "step_start", {"a": 1}, turn=1, step=3) is True
    assert rec.events == [("step_start", {"a": 1}, 1, 3)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_tolerates_missing_recorder_legacy_signature_and_failure():
    assert await events.emit(None, "x", {}) is False
    assert await events.emit(object(), "x", {}) is False
    legacy = _LegacyRec()
    assert await events.emit(legacy, "user", {"c": 1}, turn=1) is True
    assert legacy.events == [("user", {"c": 1})]
    assert await events.emit(_Rec(fail=True), "x", {}) is False  # swallowed, logged
