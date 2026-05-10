"""Helper to safely inc() agent metrics from anywhere.

Wave J (J1). Many primitives need to bump counters but importing
`from app.main import app` creates fragile cycles. This helper does
the late-import + try/except dance once so callers stay clean.

Use:
    from app.agent_framework._metrics_helper import inc_metric
    inc_metric("loop_guard_tripped")

If app.state isn't ready yet (CLI scripts, tests) → silent no-op.
"""

from __future__ import annotations


def inc_metric(name: str, *, by: int = 1) -> None:
    """Best-effort metric increment. Never raises."""
    try:
        from app.main import app as _app

        m = getattr(_app.state, "agent_metrics", None)
        if m is not None:
            m.inc(name, by=by)
    except Exception:
        pass


__all__ = ["inc_metric"]
