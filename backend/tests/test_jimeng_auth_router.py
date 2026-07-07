"""Tests for the admin jimeng auth router — all subprocess interaction is faked.

The endpoint coroutines are called directly (same style as the shot-router
tests): ``asyncio.create_subprocess_exec`` is monkeypatched to hand back a
FakeProc whose stdout replays the device-flow material, and the provider health
is stubbed for the status endpoint. No real ``dreamina`` binary is touched.
"""

from __future__ import annotations

import asyncio
import sys

import pytest
from fastapi import HTTPException

import app.api.admin  # noqa: F401 — ensure the package __init__ has run
from app.core.deps import AuthContext

# admin/__init__.py rebinds the attribute ``jimeng_auth_router`` to the APIRouter
# instance, so reach the real module through sys.modules (same pattern as
# test_generated_media_router.py).
m = sys.modules["app.api.admin.jimeng_auth_router"]

pytestmark = [pytest.mark.asyncio]

# Real device-flow material (labels verified against `dreamina login --headless`
# 2026-07-07); expires far in the future so the safety-kill never fires in-test.
_MATERIAL_LINES = [
    b"please use a browser to finish OAuth Device Flow login.\n",
    b"verification_uri: https://jimeng.jianying.com/ai-tool/cli-auth?user_code=abc\n",
    b"user_code: a8b16ef3cf1e5ed42608f30239283597\n",
    b"device_code: b9fc128ece579cb87f1f9c09a49e3633\n",
    b"poll_interval: 1s\n",
    b"expires_at: 2099-01-01T00:00:00Z\n",
]


def _auth() -> AuthContext:
    return AuthContext(user_id="11111111-1111-1111-1111-111111111111", auth_type="jwt")


class _FakeStdout:
    def __init__(self, lines, hang_after=True):
        self._lines = list(lines)
        self._hang = hang_after
        self._blocked = asyncio.Event()

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        if self._hang:
            await self._blocked.wait()  # models a live process still polling
            return b""
        return b""  # EOF → process exited

    def release(self):
        self._blocked.set()


class FakeProc:
    def __init__(self, lines=None, *, rc=None, hang=True):
        self.stdout = _FakeStdout(lines or [], hang_after=hang)
        self.returncode = rc
        self.kill_count = 0

    def kill(self):
        self.kill_count += 1
        self.returncode = -9
        self.stdout.release()

    async def wait(self):
        return self.returncode if self.returncode is not None else 0

    async def communicate(self):
        return b"", b""


def _install_exec(monkeypatch, procs, captured=None):
    """Hand back the queued FakeProcs in order; record argv lists in `captured`."""
    queue = list(procs)

    async def fake_exec(*cmd, stdout=None, stderr=None, **kwargs):
        if captured is not None:
            captured.append(list(cmd))
        return queue.pop(0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


@pytest.fixture(autouse=True)
async def _reset_login_singleton():
    m._current_login = None
    yield
    if m._current_login is not None:
        await m._current_login.kill()
        m._current_login = None


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
async def test_status_logged_in_with_credit(monkeypatch):
    async def _health(self):
        return {"ok": True, "credit": 4200}

    monkeypatch.setattr(m.JimengCliProvider, "health", _health)
    assert await m.jimeng_status(_auth()) == {"logged_in": True, "credit": 4200}


async def test_status_not_logged_in_surfaces_error(monkeypatch):
    async def _health(self):
        return {"ok": False, "error": "not_logged_in"}

    monkeypatch.setattr(m.JimengCliProvider, "health", _health)
    assert await m.jimeng_status(_auth()) == {
        "logged_in": False,
        "error": "not_logged_in",
    }


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------
async def test_login_returns_device_flow_material(monkeypatch):
    _install_exec(monkeypatch, [FakeProc(_MATERIAL_LINES)])
    result = await m.jimeng_login(_auth())
    assert result["status"] == "pending"
    assert result["verification_uri"].startswith("https://jimeng.jianying.com/")
    assert result["user_code"] == "a8b16ef3cf1e5ed42608f30239283597"
    assert result["device_code"] == "b9fc128ece579cb87f1f9c09a49e3633"
    assert result["expires_at"] == "2099-01-01T00:00:00Z"
    # The process is kept alive as the current session (drives the device poll).
    assert m._current_login is not None


async def test_repeat_login_kills_the_previous_process(monkeypatch):
    first = FakeProc(_MATERIAL_LINES)
    second = FakeProc(_MATERIAL_LINES)
    _install_exec(monkeypatch, [first, second])

    await m.jimeng_login(_auth())
    assert m._current_login.proc is first
    await m.jimeng_login(_auth())

    assert first.kill_count == 1  # the superseded login was killed
    assert m._current_login.proc is second


async def test_login_already_logged_in_when_process_exits_clean(monkeypatch):
    # No material + immediate EOF + rc==0 → the CLI reused a valid session.
    _install_exec(monkeypatch, [FakeProc([], rc=0, hang=False)])
    assert await m.jimeng_login(_auth()) == {"status": "already"}
    assert m._current_login is None


async def test_login_times_out_when_no_material(monkeypatch):
    monkeypatch.setattr(m, "_LOGIN_MATERIAL_TIMEOUT", 0.05)
    # Emits nothing and hangs → the material never arrives.
    _install_exec(monkeypatch, [FakeProc([], hang=True)])
    with pytest.raises(HTTPException) as exc:
        await m.jimeng_login(_auth())
    assert exc.value.status_code == 504
    assert m._current_login is None


async def test_login_cli_missing_returns_503(monkeypatch):
    async def boom(*cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    with pytest.raises(HTTPException) as exc:
        await m.jimeng_login(_auth())
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------
async def test_logout_kills_session_and_runs_logout(monkeypatch):
    # Seed an active login session, then logout must kill it + run `dreamina logout`.
    login_proc = FakeProc(_MATERIAL_LINES)
    logout_proc = FakeProc([], rc=0, hang=False)
    captured: list = []
    _install_exec(monkeypatch, [login_proc, logout_proc], captured)

    await m.jimeng_login(_auth())
    assert m._current_login is not None
    result = await m.jimeng_logout(_auth())

    assert result == {"logged_in": False}
    assert login_proc.kill_count == 1  # in-flight device flow killed
    assert m._current_login is None
    # The second exec was the logout subcommand.
    assert captured[1][1] == "logout"
