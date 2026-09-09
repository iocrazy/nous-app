"""run_tool_with_timeout — the ONE way a runner awaits a tool handler (phase
2b-1 §3). A timeout is a typed TOOL RESULT the model reads, never a run stop;
the handler is cancelled and awaited to quiescence."""

import asyncio

import pytest

from app.services.ai.runner import tool_exec as tx

pytestmark = pytest.mark.unit


async def test_returns_handler_result_when_in_time():
    async def fast():
        return {"ok": True}

    assert await tx.run_tool_with_timeout("Skill", fast(), timeout_s=1) == {"ok": True}


async def test_timeout_yields_a_typed_result_and_cancels_the_handler():
    cancelled = {"seen": False}

    async def slow():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled["seen"] = True
            raise

    out = await tx.run_tool_with_timeout("ResourceFetch", slow(), timeout_s=0.05)
    assert out["error"] == "timeout" and out["timed_out"] is True
    assert out["timeout_s"] == 0.05 and out["tool"] == "ResourceFetch"
    assert 0 < out["elapsed_s"] < 1
    assert cancelled["seen"] is True


async def test_timeout_waits_for_the_handler_cleanup_to_finish():
    """Dispose must reach quiescence: a subprocess-backed tool runs its
    CancelledError cleanup (kill_tree) before the timeout result is returned."""
    state = {"cleanup_done": False}

    async def slow_with_cleanup():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)  # simulated kill → await done
            state["cleanup_done"] = True
            raise

    out = await tx.run_tool_with_timeout("Skill", slow_with_cleanup(), timeout_s=0.02)
    assert out["timed_out"] is True and state["cleanup_done"] is True


async def test_handler_exception_propagates_to_the_call_sites_typed_except():
    """The wrapper owns only the wall clock: ResourceFetch / MCP sites keep
    their own typed error results (tests/test_agent_runner_mcp.py pins one)."""

    async def boom():
        raise RuntimeError("bad")

    with pytest.raises(RuntimeError, match="bad"):
        await tx.run_tool_with_timeout("Skill", boom(), timeout_s=1)


async def test_default_limit_comes_from_the_table(monkeypatch):
    from app.services.ai.runner import tool_timeouts as tt

    monkeypatch.setattr(tt.settings, "TOOL_TIMEOUTS", {"Skill": 0.05})

    async def slow():
        await asyncio.sleep(10)

    out = await tx.run_tool_with_timeout("Skill", slow())
    assert out["timed_out"] is True and out["timeout_s"] == 0.05


async def test_outer_cancellation_propagates_and_cancels_the_handler():
    inner = {"cancelled": False}

    async def slow():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            inner["cancelled"] = True
            raise

    task = asyncio.ensure_future(tx.run_tool_with_timeout("Skill", slow(), timeout_s=5))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert inner["cancelled"] is True
