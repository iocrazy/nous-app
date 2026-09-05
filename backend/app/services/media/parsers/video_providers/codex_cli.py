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
from app.services.generation.aspect import ASPECT_RATIOS as _ASPECT_TO_RATIO
from app.services.generation.aspect import ASPECT_TOLERANCE as _ASPECT_TOLERANCE
from app.services.generation.aspect import CODEX_DEFAULT_SIZE as _DEFAULT_SIZE
from app.services.generation.aspect import CODEX_SIZES as _ASPECT_TO_SIZE
from app.services.generation.aspect import aspect_instruction as _aspect_instruction


@dataclass
class GenResult:
    """A produced media file on the local filesystem."""

    local_path: str
    mime: str
    raw: dict = field(default_factory=dict)


MODEL_DETAIL_MAX = 1500


def split_skill_events(stderr: str) -> Tuple[str, str]:
    """Split a ``--json-events`` stderr into the model's words and the CLI's own.

    Every line the event stream writes is NDJSON, so anything that does NOT
    parse is the CLI talking (a panic, a loader warning). Only that half may
    be logged or attached to an error: the parsed half is a verbatim dump of
    the model's response, and both fields it would otherwise land in are read
    by humans looking for the tool's diagnostics.

    Returns ``(model_text, plain_stderr)``.
    """
    plain: List[str] = []
    parts: List[str] = []
    for line in (stderr or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            plain.append(stripped)
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") != "response.output_item.done":
            continue
        item = (event.get("data") or {}).get("item")
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for chunk in item.get("content") or []:
            if isinstance(chunk, dict) and chunk.get("type") == "output_text":
                text = chunk.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    return "\n".join(parts)[:MODEL_DETAIL_MAX], "\n".join(plain)


class CodexCliError(RuntimeError):
    """Structured provider failure carrying a stable ``code`` for the caller/UI.

    Codes: ``not_logged_in`` / ``no_credit`` / ``generation_failed`` /
    ``timeout`` / ``parse_error`` / ``cli_missing``.
    """

    def __init__(
        self, code: str, message: str, *, stderr: str = "", detail: str = ""
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        # Free-form text the MODEL wrote for the user to read — its explanation
        # for declining, and the rewrite it offers. Payload, never a signal:
        # nothing branches on it, `code` stays the only verdict. It travels via
        # task metadata (jsonb), never via the exception message, because
        # `public.dbos_error_to_text()` shreds any byte >= 0x80.
        self.detail = detail
        self.stderr = stderr


# The aspect tables now live in ``app/services/generation/aspect.py`` —
# ``ASPECT_PHRASES`` (was ``_ASPECT_TO_PHRASE`` here), ``ASPECT_RATIOS``,
# ``CODEX_SIZES``. The evidence for why they say what they say stays here,
# with the provider it was measured against.
#
# ★ Why the aspect lives in the PROMPT and not in ``--size`` (measured
# 2026-08-23, five probes against the real CLI):
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
# `ok:true` every time. Hence both halves of this fix: say it in words
# (``_aspect_instruction``), then CHECK the result (``_ASPECT_TO_RATIO`` +
# ``_ASPECT_TOLERANCE``) and say so when it still did not comply.
#
# ``_ASPECT_TO_SIZE`` is still sent because it is free, it is the documented
# contract of the CLI, and a future fix upstream would start working with no
# change here. It is NOT what makes the output the right shape.
#
# The tolerance is 6%: the model picks its own pixel dimensions (1086x1448
# rather than a round 1024x1365), so an exact match is not the bar; 6% is loose
# enough for that rounding and tight enough that a flipped orientation — the
# failure actually observed — can never slip through (3:4 vs 4:3 is 78% apart).


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
        # Set by `_run_cli` on every run: the model's own words from the last
        # invocation's event stream, or "" when it said nothing.
        self._last_model_text = ""

    # ------------------------------------------------------------------ CLI ---

    async def _run_cli(self, args: List[str], timeout: float) -> Tuple[int, str, str]:
        """Run the CLI off-loop with a hard timeout + kill-on-timeout."""
        # --json-events is a GLOBAL flag (it precedes the subcommand). It moves
        # nothing on stdout — still the single `{ok,error}` envelope this
        # provider parses — and puts the response event stream on stderr, the
        # only channel carrying the model's own words when it declines a
        # prompt. `_run_cli` splits that stream back apart before anything
        # else sees it.
        cmd = [self._bin, "--json", "--json-events", "--provider", "codex"]
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
        raw_stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
        # The event stream is separated here, once, so no caller can
        # accidentally log or classify on it: it is tens of KB of NDJSON per
        # run, and every word of it is the model's.
        self._last_model_text, stderr = split_skill_events(raw_stderr)
        if stderr.strip():
            logger.info("[codex-cli] {} stderr: {}", args[0], stderr[:2000])
        return proc.returncode, stdout, stderr

    @staticmethod
    def _detail_text(detail: object) -> str:
        """The envelope's ``error.detail`` as one readable string.

        For ``http_error`` it is the HTTP body — a string that is itself
        usually JSON (``{"detail":"The 'gpt-5.4' model is not supported…"}``);
        for ``credential_missing`` it is an object. ``message`` alone says
        "HTTP 400", which is what the user saw on 2026-09-05 when OpenAI
        dropped gpt-5.4 for ChatGPT-account Codex. Unwrap one level so the
        sentence comes out; JSON-dump anything else so no Python repr leaks.
        """
        if detail is None or detail == "":
            return ""
        if isinstance(detail, str):
            try:
                inner = json.loads(detail)
            except json.JSONDecodeError:
                return detail
            if isinstance(inner, dict):
                pick = inner.get("detail") or inner.get("message")
                if not pick and isinstance(inner.get("error"), dict):
                    pick = inner["error"].get("message")
                if isinstance(pick, str) and pick:
                    return pick
                return json.dumps(inner, ensure_ascii=False)
            return detail
        try:
            return json.dumps(detail, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(detail)

    def _classify_error(self, payload: dict, stderr: str) -> CodexCliError:
        """Map an ``ok: false`` payload onto a structured error.

        Classification reads the CLI's own ``{ok,error}`` envelope on stdout,
        never the event stream: for this binary stdout is the tool talking,
        while the stream is a dump of the model's response. Letting the latter
        steer would allow the model's prose to forge, say, an auth failure and
        send the user off to re-login for what is really a refusal.
        """
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code", ""))
        message = str(error.get("message", "")) or "gpt-image-2-skill failed"
        body = self._detail_text(error.get("detail"))[:MODEL_DETAIL_MAX]
        if body:
            # The body is the diagnosis; "HTTP 400" is only the symptom.
            message = f"{message}: {body}"
        # The model answered, but not with an image: it declined and explained
        # instead. `missing_image_result` describes the pipeline's shape, not
        # what happened, and on its own it is unactionable — the explanation
        # is the whole value here.
        if code == "missing_image_result":
            return CodexCliError(
                "content_refused",
                "the image model declined this prompt and answered with an "
                "explanation instead of an image",
                stderr=stderr[:500],
                detail=self._last_model_text,
            )
        needle_text = f"{code} {message}".lower()
        if any(n in needle_text for n in _AUTH_NEEDLES):
            return CodexCliError(
                "not_logged_in",
                f"codex session is not usable ({code}): {message} — refresh the "
                "mounted auth.json (see docs/runbook/codex-image.md)",
                stderr=stderr[:500],
                detail=body,
            )
        if any(n in needle_text for n in _QUOTA_NEEDLES):
            return CodexCliError(
                "no_credit",
                f"codex account quota exhausted ({code}): {message}",
                stderr=stderr[:500],
                detail=body,
            )
        return CodexCliError(
            "generation_failed",
            f"gpt-image-2-skill failed ({code}): {message}",
            stderr=stderr[:500],
            detail=body,
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
