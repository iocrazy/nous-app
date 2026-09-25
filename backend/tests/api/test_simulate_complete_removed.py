"""``POST /issues/{id}/agent-runs/{run_id}/simulate-complete`` is gone.

It was a dev helper from before the agent runtime existed: any user who could
see an issue flipped a LIVE run to ``completed`` with a canned summary, 4 cents
of made-up cost and invented token counts, while the real worker kept going.
The Todolist chat showed its "Simulate finish" button on every running run in
production. A real runtime now finishes runs; nothing may forge that.
"""

from __future__ import annotations

import pytest

from app.main import app

pytestmark = pytest.mark.unit


def test_no_route_forges_a_run_completion() -> None:
    paths = [getattr(r, "path", "") for r in app.routes]
    assert not [p for p in paths if "simulate" in p], paths
