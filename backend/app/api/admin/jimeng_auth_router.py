"""Admin jimeng CLI auth — device-flow login managed from the admin panel.

Moves the one-time ``dreamina login`` off the NAS shell into the admin UI:
  - ``GET  /admin/jimeng/status`` wraps the provider health (``user_credit``).
  - ``POST /admin/jimeng/login``  launches the OAuth device flow and returns the
    verification material (verification_uri / user_code / device_code /
    expires_at) so the panel can show a clickable authorization link.
  - ``POST /admin/jimeng/logout`` clears the session.

The login subprocess runs in THIS (gateway) container; the token lands on the
shared ``dreamina-auth`` volume that the worker also mounts, so a login here is
immediately visible to the generation workflows on the worker.

Subprocess discipline mirrors ``JimengCliProvider`` (off-loop, hard timeout +
kill). The login process is the ONE deliberate long-lived exception — it must
stay alive to poll the device authorization and write the token on success — so
it is bounded two ways: an ``expires_at`` safety-kill timer, and supersession
(a new login kills the previous one). Its stdout is drained in the background so
the child never blocks on a full pipe, and it is reaped on natural exit.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.core.admin_deps import AdminAuthDep
from app.services.media.parsers.video_providers.jimeng_cli import JimengCliProvider

router = APIRouter()

# How long to wait for the device-flow material before giving up (504).
_LOGIN_MATERIAL_TIMEOUT = 15.0
# Margin added to expires_at for the safety-kill; also the fallback lifetime when
# expires_at can't be parsed.
_EXPIRY_KILL_MARGIN = 30.0
_FALLBACK_LOGIN_LIFETIME = 600.0
_LOGOUT_TIMEOUT = 30.0

# The material lines dreamina login prints (verified 2026-07-07), one "key: value"
# per line: verification_uri / user_code / device_code / poll_interval / expires_at.
_MATERIAL_KEYS = ("verification_uri", "user_code", "device_code", "expires_at")
_REQUIRED_KEYS = frozenset(_MATERIAL_KEYS)
_LINE_RE = re.compile(r"^(\w+):\s*(.+)$")


def _bin() -> str:
    """The dreamina binary — DREAMINA_BIN override (tests / non-standard installs)
    then the PATH name baked into the image (same resolution as the provider)."""
    return os.environ.get("DREAMINA_BIN", "dreamina")


class _LoginExited(RuntimeError):
    """The login process ended before printing device material (already logged in
    when rc==0, or a hard failure otherwise)."""

    def __init__(self, returncode: Optional[int]) -> None:
        super().__init__(f"login exited rc={returncode}")
        self.returncode = returncode


class _LoginSession:
    """A live ``dreamina login`` process kept alive to complete the device flow."""

    def __init__(self, proc: asyncio.subprocess.Process, material: dict) -> None:
        self.proc = proc
        self.material = material
        self._drain_task: Optional[asyncio.Task] = None
        self._kill_task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._drain_task = asyncio.create_task(self._drain())
        self._kill_task = asyncio.create_task(self._kill_at_expiry())

    async def _drain(self) -> None:
        # Keep the pipe empty so the child never blocks on a full stdout buffer;
        # loop ends at EOF (process exit), then reap.
        try:
            if self.proc.stdout is not None:
                while await self.proc.stdout.readline():
                    pass
        except Exception:  # noqa: BLE001 — best-effort drain
            pass
        try:
            await self.proc.wait()
        except Exception:  # noqa: BLE001
            pass

    async def _kill_at_expiry(self) -> None:
        delay = _expiry_delay(self.material.get("expires_at"))
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if self.proc.returncode is None:
            logger.info("[jimeng-auth] login window expired — killing login process")
            await self.kill()

    async def kill(self) -> None:
        for task in (self._kill_task, self._drain_task):
            if task is not None:
                task.cancel()
        try:
            if self.proc.returncode is None:
                self.proc.kill()
                await asyncio.wait_for(self.proc.wait(), timeout=2)
        except Exception:  # noqa: BLE001 — best-effort reap
            pass


def _expiry_delay(expires_at: Optional[str]) -> float:
    """Seconds until the safety-kill: (expires_at - now) + margin, with a bounded
    fallback when the timestamp is missing/unparseable."""
    if not expires_at:
        return _FALLBACK_LOGIN_LIFETIME
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        remaining = (exp - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, remaining) + _EXPIRY_KILL_MARGIN
    except ValueError:
        return _FALLBACK_LOGIN_LIFETIME


# Single active login session (a device flow is inherently one-at-a-time for one
# account); guarded so a burst of POSTs can't race two processes into existence.
_login_lock = asyncio.Lock()
_current_login: Optional[_LoginSession] = None


async def _read_login_material(
    proc: asyncio.subprocess.Process, timeout: float
) -> dict:
    """Read stdout lines until all device-flow fields are seen, or fail.

    Raises ``TimeoutError`` if the material isn't complete within ``timeout``, or
    ``_LoginExited`` if the process ends first (already-logged-in on rc==0)."""
    material: dict = {}
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    assert proc.stdout is not None
    while not _REQUIRED_KEYS <= material.keys():
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("timed out reading login material")
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
        except asyncio.TimeoutError:
            raise TimeoutError("timed out reading login material")
        if not line:  # EOF — the process exited before emitting the material
            raise _LoginExited(await proc.wait())
        match = _LINE_RE.match(line.decode("utf-8", "replace").strip())
        if match and match.group(1) in _REQUIRED_KEYS:
            material[match.group(1)] = match.group(2).strip()
    return material


async def _run_cli_once(args: list, timeout: float) -> tuple[Optional[int], str, str]:
    """One-shot off-loop CLI run with a hard timeout + kill (logout path)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            _bin(),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **safe_popen_kwargs(),
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503, detail="dreamina binary not found"
        ) from exc
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await asyncio.wait_for(proc.communicate(), timeout=2)
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(status_code=504, detail="dreamina command timed out")
    out = out_b.decode("utf-8", "replace") if out_b else ""
    err = err_b.decode("utf-8", "replace") if err_b else ""
    return proc.returncode, out, err


@router.get("/status")
async def jimeng_status(auth: AdminAuthDep) -> dict:
    """Login state + credit balance, via the provider's ``user_credit`` health.

    Returns ``{logged_in, credit?, error?}``. Never raises on a not-logged-in /
    error CLI state — the panel renders a red badge from ``logged_in=false``."""
    health = await JimengCliProvider().health()
    result: dict = {"logged_in": bool(health.get("ok"))}
    if "credit" in health:
        result["credit"] = health["credit"]
    if not health.get("ok") and health.get("error"):
        result["error"] = health["error"]
    return result


@router.post("/login")
async def jimeng_login(auth: AdminAuthDep) -> dict:
    """Start the OAuth device flow and return the authorization material.

    Launches ``dreamina login`` (which prints the material then keeps polling the
    device authorization, writing the token on success). Returns
    ``{status:'pending', verification_uri, user_code, device_code, expires_at}``.
    A new login supersedes any in-flight one (old process killed first). If the
    CLI is already logged in the process exits immediately → ``{status:'already'}``.
    504 if the material doesn't arrive within the window."""
    global _current_login
    async with _login_lock:
        if _current_login is not None:
            await _current_login.kill()
            _current_login = None
        try:
            proc = await asyncio.create_subprocess_exec(
                _bin(),
                "login",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503, detail="dreamina binary not found"
            ) from exc

        try:
            material = await _read_login_material(proc, _LOGIN_MATERIAL_TIMEOUT)
        except _LoginExited as exited:
            # No device material: rc==0 means the CLI reused a valid session.
            if exited.returncode == 0:
                return {"status": "already"}
            raise HTTPException(status_code=500, detail="dreamina login failed")
        except TimeoutError:
            try:
                proc.kill()
                await asyncio.wait_for(proc.communicate(), timeout=2)
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(
                status_code=504, detail="timed out starting dreamina login"
            )

        session = _LoginSession(proc, material)
        session.start()
        _current_login = session
        logger.info("[jimeng-auth] device-flow login started (admin panel)")
        return {"status": "pending", **material}


@router.post("/logout")
async def jimeng_logout(auth: AdminAuthDep) -> dict:
    """Clear the login session: kill any in-flight device flow + ``dreamina logout``."""
    global _current_login
    async with _login_lock:
        if _current_login is not None:
            await _current_login.kill()
            _current_login = None
    rc, _out, err = await _run_cli_once(["logout"], _LOGOUT_TIMEOUT)
    if rc not in (0, None):
        logger.warning("[jimeng-auth] logout rc={} err={}", rc, err[:200])
    return {"logged_in": False}
