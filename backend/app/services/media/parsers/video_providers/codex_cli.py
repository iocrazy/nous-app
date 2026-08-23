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


# aspect → a size string for ``--size``.
#
# ⚠️ On THIS provider ``--size`` is not honoured — measured 2026-08-23, see
# ``_ASPECT_TO_PHRASE`` below for the evidence and the actual mechanism. The
# argument is still sent because it is free, it is the documented contract of
# the CLI, and a future fix on the upstream side would start working with no
# change here. It is NOT what makes the output the right shape.
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

# aspect → the words that actually control the output shape.
#
# ★ Why the prompt and not ``--size`` (measured 2026-08-23, five probes):
#
#     --size 999x999                  → CLI: "must use width and height values
#                                        that are multiples of 16"  (so the
#                                        old "exactly three sizes" comment that
#                                        used to sit above _ASPECT_TO_SIZE was
#                                        simply wrong)
#     --size 8192x10912               → CLI: "maximum edge of 3840px"
#     --size 1056x1408 + neutral text → ok:true, produced 1536x1024 LANDSCAPE
#     --size 1024x1536 + neutral text → ok:true, produced 1536x1024 LANDSCAPE
#     --size 1024x1536 + portrait text→ produced 1086x1448 = exactly 0.7500
#
# So the size argument is discarded somewhere past the CLI and the model infers
# the shape from the prompt. (Mechanism, likely: this provider talks to the
# ChatGPT private backend `chatgpt.com/backend-api/codex/responses`, not the
# public images API, and the size parameter has no landing spot there.)
#
# The damage was silent and already shipped: all four codex images in prod came
# back at a ratio nobody asked for — three requested 16:9 landscape and are
# PORTRAIT (1184x1328, 1147x1371, 1199x1312), one requested 1:1 and is 0.80.
# `ok:true` every time. Hence both halves of this fix: say it in words, then
# CHECK the result and say so when it still did not comply.
_ASPECT_TO_PHRASE = {
    "21:9": "21:9 ultra-wide landscape (much wider than tall)",
    "16:9": "16:9 landscape (wider than tall)",
    "3:2": "3:2 landscape (wider than tall)",
    "4:3": "4:3 landscape (wider than tall)",
    "1:1": "1:1 square (equal width and height)",
    "3:4": "3:4 portrait (taller than wide)",
    "2:3": "2:3 portrait (taller than wide)",
    "9:16": "9:16 tall portrait (much taller than wide)",
}

# Numeric width/height target per aspect, for verifying what came back.
_ASPECT_TO_RATIO = {
    "21:9": 21 / 9,
    "16:9": 16 / 9,
    "3:2": 3 / 2,
    "4:3": 4 / 3,
    "1:1": 1.0,
    "3:4": 3 / 4,
    "2:3": 2 / 3,
    "9:16": 9 / 16,
}

# How far off the requested ratio still counts as compliance. The model picks
# its own pixel dimensions (1086x1448 rather than a round 1024x1365), so an
# exact match is not the bar; 6% is loose enough for that rounding and tight
# enough that a flipped orientation — the failure actually observed — can never
# slip through (3:4 vs 4:3 is 78% apart).
_ASPECT_TOLERANCE = 0.06


def _aspect_instruction(aspect: str) -> str:
    """The sentence appended to the prompt to pin the output shape.

    Appended, never prepended, and only when the aspect is known: the user's
    own words stay first and intact. An unknown or empty aspect adds nothing —
    "let the model choose" is a real request (IC 自适应 sends an empty aspect
    on purpose) and inventing a shape for it would be worse than silence.
    """
    phrase = _ASPECT_TO_PHRASE.get((aspect or "").strip())
    if not phrase:
        return ""
    return (
        f"\n\nOutput image aspect ratio: {phrase}. "
        "The whole image must have this shape."
    )


def _measure(path: str) -> Optional[Tuple[int, int]]:
    """(width, height) of the produced file, or None if it cannot be read.

    None is not an error here: the verification below is a REPORT, not a gate.
    Refusing to hand back an image we already paid for because we could not
    measure it would trade a cosmetic problem for a real one.
    """
    try:
        from PIL import Image

        with Image.open(path) as img:
            return int(img.width), int(img.height)
    except Exception as exc:  # noqa: BLE001 — Pillow's decode errors are varied
        logger.warning("[codex-cli] could not measure produced image: {}", exc)
        return None

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
        # The shape actually comes from here, not from --size. See the block
        # above _ASPECT_TO_PHRASE for the measurements.
        effective_prompt = prompt + _aspect_instruction(aspect)
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
            effective_prompt,
            "--out",
            out_path,
            "--size",
            size,
            "--format",
            "png",
            "--quality",
            quality or "high",
            # --background defaults to "auto", which lets the codex chain
            # pick transparent — and the codex transparent path renders on a
            # pure-green #00ff00 matte whose spill leaks into the output
            # (2026-08-20 "all codex images look green"). Canvas images are
            # full frames; force opaque.
            "--background",
            "opaque",
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
        # Verify rather than assume. The provider answered ok:true for every
        # one of the four prod images whose ratio was wrong, so "it returned a
        # file" has already been shown not to mean "it did what was asked".
        raw = dict(payload)
        measured = _measure(produced)
        if measured:
            width, height = measured
            raw["measured_size"] = f"{width}x{height}"
            want = _ASPECT_TO_RATIO.get((aspect or "").strip())
            if want and height > 0:
                got = width / height
                raw["requested_aspect"] = aspect
                raw["aspect_honored"] = abs(got - want) <= want * _ASPECT_TOLERANCE
                if not raw["aspect_honored"]:
                    # Warning, not an exception: the image is already generated
                    # and already paid for, and a usable picture of the wrong
                    # shape beats no picture. But it must not pass in silence —
                    # that silence is exactly what hid this for months.
                    logger.warning(
                        "[codex-cli] asked for aspect {} ({:.3f}) but got "
                        "{}x{} ({:.3f}) — the model did not honour the "
                        "requested shape",
                        aspect,
                        want,
                        width,
                        height,
                        got,
                    )
        return GenResult(local_path=produced, mime="image/png", raw=raw)

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
