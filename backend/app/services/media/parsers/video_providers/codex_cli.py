"""Codex (GPT Image 2) CLI provider — subprocess-driven image generation.

Drives the ``gpt-image-2-skill`` CLI (baked into the backend image, see
``docs/runbook/codex-image.md``) as a subprocess with ``--provider codex``: the
credential is the local Codex CLI OAuth session (``auth.json``), so there is no
api_key. The session file is mounted read-write — the CLI refreshes tokens in
place.

Subprocess discipline (backend event-loop freeze blood-lesson, same as
``jimeng_cli``): ``create_subprocess_exec`` + ``asyncio.wait_for`` off-loop,
hard timeout with kill+reap, ``safe_popen_kwargs()`` for PR_SET_PDEATHSIG.

Unlike dreamina, ``--json`` mode prints a single pure-JSON object on stdout
(captured 2026-08-17, v0.7.3): success is ``{"ok": true, "output": {"path":
...}}`` with the PNG already written to ``--out``; failure is ``{"ok": false,
"error": {"code": ..., "message": ...}}`` with exit 1. Every failure is
classified into a stable code (``not_logged_in`` / ``no_credit`` /
``generation_failed`` / ``timeout`` / ``parse_error`` / ``cli_missing``) and
raised as :class:`CodexCliError` — never a bare 500.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from loguru import logger

from app.agent_framework.process_lifecycle import safe_popen_kwargs


@dataclass
class GenResult:
    """A produced media file on the local filesystem."""

    local_path: str
    mime: str
    raw: dict = field(default_factory=dict)


class CodexCliError(RuntimeError):
    """Structured provider failure carrying a stable ``code`` for the caller/UI.

    Codes: ``not_logged_in`` / ``no_credit`` / ``generation_failed`` /
    ``timeout`` / ``parse_error`` / ``cli_missing``.
    """

    def __init__(self, code: str, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stderr = stderr


# aspect → gpt-image-2 canonical size. The model supports exactly three sizes
# (square / landscape / portrait); every catalog aspect maps to the nearest.
_ASPECT_TO_SIZE = {
    "21:9": "1536x1024",
    "16:9": "1536x1024",
    "3:2": "1536x1024",
    "4:3": "1536x1024",
    "1:1": "1024x1024",
    "3:4": "1024x1536",
    "2:3": "1024x1536",
    "9:16": "1024x1536",
}
_DEFAULT_SIZE = "1024x1024"

# Error-code / message needles for classification. Only ever consulted on the
# failure branch (ok != true) — a successful payload containing "401"-ish
# tokens must never be misread (M1 discipline, inherited from jimeng_cli).
_AUTH_NEEDLES = (
    "access_token",
    "auth",
    "login",
    "unauthorized",
    "401",
    "token_expired",
)
_QUOTA_NEEDLES = (
    "usage_limit",
    "usage limit",
    "quota",
    "rate_limit",
    "rate limit",
    "insufficient",
    "429",
)


def _default_timeout() -> float:
    try:
        return float(
            max(30, min(3600, int(os.environ.get("CODEX_CLI_TIMEOUT", "900"))))
        )
    except (TypeError, ValueError):
        return 900.0


class CodexCliProvider:
    """gpt-image-2-skill CLI provider — image generation only."""

    def __init__(
        self,
        *,
        bin_path: Optional[str] = None,
        auth_file: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        # GPT_IMAGE_2_SKILL_BIN lets tests / non-standard installs point at a
        # specific binary; CODEX_AUTH_FILE points at the mounted session file
        # (unset → the CLI falls back to its own ~/.codex/auth.json default).
        self._bin = bin_path or os.environ.get(
            "GPT_IMAGE_2_SKILL_BIN", "gpt-image-2-skill"
        )
        self._auth_file = (
            auth_file
            if auth_file is not None
            else os.environ.get("CODEX_AUTH_FILE", "")
        )
        self._timeout = timeout if timeout is not None else _default_timeout()

    # ------------------------------------------------------------------ CLI ---

    async def _run_cli(self, args: List[str], timeout: float) -> Tuple[int, str, str]:
        """Run the CLI off-loop with a hard timeout + kill-on-timeout."""
        cmd = [self._bin, "--json", "--provider", "codex"]
        if self._auth_file:
            cmd += ["--auth-file", self._auth_file]
        cmd += args
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
        except FileNotFoundError as exc:
            raise CodexCliError(
                "cli_missing",
                f"gpt-image-2-skill binary not found ({self._bin!r})",
            ) from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await asyncio.wait_for(proc.communicate(), timeout=2)
            except Exception:  # noqa: BLE001 — best-effort reap, never re-raise
                pass
            logger.error(
                "[codex-cli] '{}' timed out after {}s (killed)", args[0], timeout
            )
            raise CodexCliError(
                "timeout",
                f"gpt-image-2-skill {args[0]} timed out after {timeout:.0f}s",
            )

        stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
        stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
        if stderr.strip():
            logger.info("[codex-cli] {} stderr: {}", args[0], stderr[:2000])
        return proc.returncode, stdout, stderr

    @staticmethod
    def _classify_error(payload: dict, stderr: str) -> CodexCliError:
        """Map an ``ok: false`` payload onto a structured error."""
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code", ""))
        message = str(error.get("message", "")) or "gpt-image-2-skill failed"
        needle_text = f"{code} {message}".lower()
        if any(n in needle_text for n in _AUTH_NEEDLES):
            return CodexCliError(
                "not_logged_in",
                f"codex session is not usable ({code}): {message} — refresh the "
                "mounted auth.json (see docs/runbook/codex-image.md)",
                stderr=stderr[:500],
            )
        if any(n in needle_text for n in _QUOTA_NEEDLES):
            return CodexCliError(
                "no_credit",
                f"codex account quota exhausted ({code}): {message}",
                stderr=stderr[:500],
            )
        return CodexCliError(
            "generation_failed",
            f"gpt-image-2-skill failed ({code}): {message}",
            stderr=stderr[:500],
        )

    # ------------------------------------------------------------- generation --

    async def generate_image(
        self,
        *,
        prompt: str,
        aspect: str,
        model_version: Optional[str] = None,
        quality: Optional[str] = None,
        ref_image_path: Optional[str] = None,
        ref_image_paths: Optional[List[str]] = None,
    ) -> GenResult:
        """``images generate`` (or ``edit`` with local ref images) → local PNG.

        ``ref_image_paths`` supersedes the single ``ref_image_path`` (kept for
        callers not yet migrated); the CLI takes multiple ``--ref-image``
        flags (IC caps references at 9)."""
        out_dir = tempfile.mkdtemp(prefix="codeximg_")
        out_path = os.path.join(out_dir, "gen.png")
        size = _ASPECT_TO_SIZE.get(aspect or "", _DEFAULT_SIZE)
        refs = [
            r
            for r in (ref_image_paths or ([ref_image_path] if ref_image_path else []))
            if r
        ][:9]
        mode = "edit" if refs else "generate"
        args = [
            "images",
            mode,
            "--prompt",
            prompt,
            "--out",
            out_path,
            "--size",
            size,
            "--format",
            "png",
            "--quality",
            quality or "high",
        ]
        if model_version:
            args += ["--model", model_version]
        for ref in refs:
            args += ["--ref-image", ref]

        rc, out, err = await self._run_cli(args, self._timeout)

        try:
            payload = json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            raise CodexCliError(
                "parse_error",
                f"could not parse gpt-image-2-skill {mode} output as JSON",
                stderr=err[:500],
            )

        if payload.get("ok") is not True or rc != 0:
            raise self._classify_error(payload, err)

        # Trust the filesystem, not the CLI's claim: the product must exist.
        produced = str((payload.get("output") or {}).get("path") or "") or out_path
        if not os.path.isfile(produced):
            raise CodexCliError(
                "generation_failed",
                f"gpt-image-2-skill reported ok but produced no file at {produced}",
                stderr=err[:500],
            )
        return GenResult(local_path=produced, mime="image/png", raw=payload)

    # ------------------------------------------------------------------ health --

    async def health(self) -> dict:
        """Wrap ``doctor`` → ``{ok, error?}`` (never raises)."""
        try:
            rc, out, err = await self._run_cli(["doctor"], timeout=30.0)
        except CodexCliError as exc:
            return {"ok": False, "error": exc.code}
        try:
            payload = json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict) or rc != 0:
            return {"ok": False, "error": "health_failed"}
        auth = ((payload.get("providers") or {}).get("codex") or {}).get("auth") or {}
        if auth.get("access_token_present") is not True:
            return {"ok": False, "error": "not_logged_in"}
        return {"ok": True}
