"""jimeng_cli_router — server-side dreamina account management.

The dreamina (即梦) login lives in THIS server's container (single shared
account, unlike the per-user codex daemon). Until now the only way to
(re)login was asking the assistant in chat to babysit a terminal OAuth
flow; this router puts the IC-style CLI card on the settings page instead:

- GET  /jimeng-cli/status  → logged_in + credit (runs ``dreamina user_credit``)
- POST /jimeng-cli/login   → starts ``dreamina login`` (device flow), returns
  the verification link + user code; the process keeps polling in the
  background and writes the token on approval (persisted via the compose
  bind mount — survives container rebuilds).

NB: this manages a SHARED server credential. Every signed-in user may view
status; starting a login is platform-admin only. It used to be open to every
signed-in user: completing the device flow with one's own dreamina account
re-pointed the server's shared credential — every user's jimeng generation
then ran on, and was visible to, that account — and each call left one more
``dreamina login`` process polling in the container.

Both spawns go through ``safe_popen_kwargs()`` so the CLI never sees the
server's secrets (CLAUDE.md, 防御模式「子进程环境要擦洗」).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs
from app.core.admin_deps import AdminAuthDep
from app.core.deps import AuthDep

router = APIRouter(prefix="/jimeng-cli", tags=["Jimeng CLI"])

_LOGIN_URI_RE = re.compile(r"(https://\S+)")
_USER_CODE_RE = re.compile(
    r"(?:用户码|user[_ ]?code)[^A-Z0-9]*([A-Z0-9][A-Z0-9-]{3,})", re.I
)


async def _run_dreamina(args: list[str], timeout_s: int = 30) -> Tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "dreamina",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **safe_popen_kwargs(),
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return (
        proc.returncode or 0,
        out.decode(errors="replace"),
        err.decode(errors="replace"),
    )


async def _start_login_flow() -> Optional[dict]:
    """Spawn ``dreamina login`` and scrape the device-flow link + code from
    its early output. The process is left running — it polls the OAuth server
    and persists the token once the user approves in their browser."""
    proc = await asyncio.create_subprocess_exec(
        "dreamina",
        "login",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **safe_popen_kwargs(),
    )
    collected = ""
    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=20)
            if not line:
                break
            collected += line.decode(errors="replace")
            # Already logged in → the CLI reuses the token and exits.
            if "已复用" in collected or "登录成功" in collected:
                return {"already_logged_in": True}
            uri = _LOGIN_URI_RE.search(collected)
            code = _USER_CODE_RE.search(collected)
            if uri:
                return {
                    "verification_uri": uri.group(1),
                    "user_code": code.group(1) if code else None,
                }
    except asyncio.TimeoutError:
        pass
    logger.warning("[jimeng-cli] login flow gave no link; output: {}", collected[:300])
    return None


@router.get("/status")
async def jimeng_status(auth: AuthDep) -> dict:
    """Login + credit state of the server's shared dreamina account."""
    try:
        code, out, err = await _run_dreamina(["user_credit"])
    except FileNotFoundError:
        return {
            "data": {"available": False, "logged_in": False, "reason": "cli_missing"}
        }
    except asyncio.TimeoutError:
        return {"data": {"available": True, "logged_in": False, "reason": "timeout"}}
    if code == 0:
        try:
            body = json.loads(out[out.index("{") :])
        except Exception:
            body = {}
        return {
            "data": {
                "available": True,
                "logged_in": True,
                "total_credit": body.get("total_credit"),
                "user_id": str(body.get("user_id") or ""),
                "vip_level": body.get("vip_level") or "",
            }
        }
    return {
        "data": {
            "available": True,
            "logged_in": False,
            "reason": (err or out)[:200],
        }
    }


@router.post("/login")
async def jimeng_login(auth: AdminAuthDep) -> dict:
    """Start the dreamina device-flow login; returns the link + user code
    (valid ~10 minutes). Approve it in a browser on any device."""
    result = await _start_login_flow()
    if result is None:
        raise HTTPException(502, "dreamina login did not produce a device link")
    return {"data": result}
