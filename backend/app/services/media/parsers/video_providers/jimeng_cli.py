"""Jimeng (即梦 / dreamina) CLI provider — subprocess-driven image + video generation.

Drives the official ``dreamina`` CLI (baked into the backend image, see
``docs/runbook/jimeng-cli.md``) as a subprocess. The CLI authenticates via a
one-time OAuth login persisted on a shared volume, so there is no api_key — the
subscription session is the credential.

Subprocess discipline (backend event-loop freeze blood-lesson):
  - never a synchronous wait on the loop: ``create_subprocess_exec`` +
    ``asyncio.wait_for(proc.communicate(), timeout=...)`` (off-loop);
  - every call carries a hard timeout; on timeout the process is ``kill()``ed
    and reaped so no orphan lingers;
  - ``safe_popen_kwargs()`` sets PR_SET_PDEATHSIG so a child dies with us.

The CLI's stdout mixes human log lines with JSON; ``_extract_json`` scans for
the best JSON object. Every failure is classified into a stable code
(``not_logged_in`` / ``no_credit`` / ``generation_failed`` / ``timeout`` /
``parse_error``) and raised as :class:`JimengCliError` — never a bare 500.

Generation flow (both image + video): submit (``text2image`` /
``text2video`` / ``image2video`` with ``--poll``), then a
``query_result --submit_id --download_dir=<tmp>`` fetch, then scan the temp dir
for the produced media file → ``local_path`` (the shot pipeline ingests the
local file directly, no URL download).
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


class JimengCliError(RuntimeError):
    """Structured provider failure carrying a stable ``code`` for the caller/UI.

    Codes: ``not_logged_in`` / ``no_credit`` / ``generation_failed`` /
    ``timeout`` / ``parse_error`` / ``cli_missing``.
    """

    def __init__(self, code: str, message: str, *, stderr: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stderr = stderr


# aspect → dreamina --ratio. Aligned with ArkImageProvider aspect semantics; any
# unknown/missing aspect falls back to a square.
_ASPECT_TO_RATIO = {
    "21:9": "21:9",
    "16:9": "16:9",
    "3:2": "3:2",
    "4:3": "4:3",
    "1:1": "1:1",
    "3:4": "3:4",
    "2:3": "2:3",
    "9:16": "9:16",
}
_DEFAULT_RATIO = "1:1"

# JSON keys that mark a "result" object worth preferring when several JSON blobs
# appear in the CLI's mixed stdout.
_RESULT_KEYS = (
    "submit_id",
    "gen_status",
    "result_json",
    "images",
    "videos",
    "credit",
    "data",
)

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_VIDEO_EXTS = (".mp4", ".mov", ".webm", ".m4v")

# Real not-logged-in output (verified 2026-07-07):
#   未检测到有效登录态，请先执行 dreamina login
_NOT_LOGGED_IN_ZH = ("未检测到有效登录态", "请先执行 dreamina login")
# NB: no bare "401" needle — it substring-matches any numeric token (a credit
# balance, a submit_id fragment) in an otherwise-successful payload and would
# misread a paid generation as not-logged-in (M1). "unauthorized" carries the
# auth signal without the false positives.
_NOT_LOGGED_IN_EN = (
    "not logged in",
    "please login",
    "please log in",
    "unauthorized",
)
_NO_CREDIT_ZH = ("额度不足", "余额不足", "积分不足", "配额不足")
_NO_CREDIT_EN = (
    "no credit",
    "insufficient credit",
    "insufficient balance",
    "quota exceeded",
)


def _extract_json(text: str) -> Optional[dict]:
    """Scan ``text`` for JSON objects and return the most result-like one.

    Uses ``json.JSONDecoder.raw_decode`` at each ``{`` so it tolerates log lines
    before/after and multiple objects; the object carrying the most result keys
    (submit_id / gen_status / images / videos / ...) wins. Returns ``None`` when
    no JSON object parses.
    """
    if not text:
        return None
    decoder = json.JSONDecoder()
    candidates: List[dict] = []
    idx = 0
    n = len(text)
    while idx < n:
        if text[idx] == "{":
            try:
                obj, end = decoder.raw_decode(text, idx)
            except json.JSONDecodeError:
                idx += 1
                continue
            if isinstance(obj, dict):
                candidates.append(obj)
            idx = max(end, idx + 1)
            continue
        idx += 1
    if not candidates:
        return None

    def _score(d: dict) -> int:
        return sum(1 for k in _RESULT_KEYS if k in d)

    # Stable: highest result-key score wins; ties keep first-seen order.
    return max(candidates, key=_score)


def _contains_any(text: str, needles: Tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


class JimengCliProvider:
    """dreamina CLI provider — implements both the image and video entry points."""

    def __init__(
        self,
        *,
        bin_path: Optional[str] = None,
        image_poll: int = 60,
        image_margin: int = 120,
        video_poll: int = 90,
        video_margin: int = 300,
        query_timeout: float = 120.0,
    ) -> None:
        # DREAMINA_BIN lets tests / non-standard installs point at a specific
        # binary; defaults to the PATH-resolved name baked into the image.
        self._bin = bin_path or os.environ.get("DREAMINA_BIN", "dreamina")
        self._image_poll = image_poll
        self._image_margin = image_margin
        self._video_poll = video_poll
        self._video_margin = video_margin
        self._query_timeout = query_timeout

    # ------------------------------------------------------------------ CLI ---

    async def _run_cli(self, args: List[str], timeout: float) -> Tuple[int, str, str]:
        """Run ``dreamina <args>`` off-loop with a hard timeout + kill-on-timeout.

        Returns ``(returncode, stdout, stderr)``. Raises ``JimengCliError`` with
        code ``timeout`` (killed) or ``cli_missing`` (binary absent).
        """
        cmd = [self._bin, *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **safe_popen_kwargs(),
            )
        except FileNotFoundError as exc:
            raise JimengCliError(
                "cli_missing", f"dreamina binary not found ({self._bin!r})"
            ) from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            # Hard-kill the runaway process and reap it so no orphan lingers.
            try:
                proc.kill()
                await asyncio.wait_for(proc.communicate(), timeout=2)
            except Exception:  # noqa: BLE001 — best-effort reap, never re-raise
                pass
            logger.error(
                "[jimeng-cli] '{}' timed out after {}s (killed)", args[0], timeout
            )
            raise JimengCliError(
                "timeout", f"dreamina {args[0]} timed out after {timeout:.0f}s"
            )

        stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
        stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
        if stderr.strip():
            logger.info("[jimeng-cli] {} stderr: {}", args[0], stderr[:2000])
        return proc.returncode, stdout, stderr

    @staticmethod
    def _classify_failure(
        returncode: int, stdout: str, stderr: str
    ) -> Optional[JimengCliError]:
        """Map CLI output onto a structured error, or ``None`` when it looks OK."""
        text = f"{stdout}\n{stderr}"
        low = text.lower()
        if _contains_any(text, _NOT_LOGGED_IN_ZH) or _contains_any(
            low, _NOT_LOGGED_IN_EN
        ):
            return JimengCliError(
                "not_logged_in",
                "dreamina is not logged in — run `dreamina login` on the host",
                stderr=stderr[:500],
            )
        if _contains_any(text, _NO_CREDIT_ZH) or _contains_any(low, _NO_CREDIT_EN):
            return JimengCliError(
                "no_credit",
                "dreamina account has no remaining credit",
                stderr=stderr[:500],
            )
        return None

    # ------------------------------------------------------------- generation --

    async def generate_image(
        self,
        *,
        prompt: str,
        aspect: str,
        model_version: Optional[str] = None,
        resolution_type: Optional[str] = None,
    ) -> GenResult:
        """text2image → local image file."""
        ratio = _ASPECT_TO_RATIO.get(aspect or "", _DEFAULT_RATIO)
        args = [
            "text2image",
            f"--prompt={prompt}",
            f"--ratio={ratio}",
            f"--poll={self._image_poll}",
        ]
        if resolution_type:
            args.append(f"--resolution_type={resolution_type}")
        if model_version:
            args.append(f"--model_version={model_version}")
        submit_timeout = self._image_poll + self._image_margin
        return await self._submit_and_fetch(
            args, submit_timeout, _IMAGE_EXTS, "image/png"
        )

    async def generate_video(
        self,
        *,
        prompt: str,
        aspect: str,
        model_version: Optional[str] = None,
        image_path: Optional[str] = None,
    ) -> GenResult:
        """text2video (or image2video when ``image_path`` is given) → local mp4."""
        if image_path:
            # image2video takes a single --image; ratio is inferred from it.
            args = ["image2video", f"--image={image_path}", f"--prompt={prompt}"]
        else:
            ratio = _ASPECT_TO_RATIO.get(aspect or "", _DEFAULT_RATIO)
            args = ["text2video", f"--prompt={prompt}", f"--ratio={ratio}"]
        if model_version:
            args.append(f"--model_version={model_version}")
        args.append(f"--poll={self._video_poll}")
        submit_timeout = self._video_poll + self._video_margin
        return await self._submit_and_fetch(
            args, submit_timeout, _VIDEO_EXTS, "video/mp4"
        )

    async def _submit_and_fetch(
        self,
        submit_args: List[str],
        submit_timeout: float,
        exts: Tuple[str, ...],
        mime: str,
    ) -> GenResult:
        """Submit a generation, download its product into a temp dir, return the file.

        A single temp dir is the CWD for the submit and the ``--download_dir`` for
        the follow-up ``query_result``, so any file the CLI writes lands there and
        we hand back the first matching media file.
        """
        download_dir = tempfile.mkdtemp(prefix="jimeng_")

        rc, out, err = await self._run_cli(submit_args, submit_timeout)
        parsed = _extract_json(out) or {}
        submit_id = parsed.get("submit_id")

        # Only interrogate the output for failure signatures when the submit did
        # NOT cleanly yield a submit_id (rc != 0 or no id). A paid, successful
        # submit whose JSON happens to contain tokens like "401" must never be
        # misclassified as not-logged-in / no-credit (M1).
        if rc != 0 or not submit_id:
            failure = self._classify_failure(rc, out, err)
            if failure is not None:
                raise failure

        gen_status = str(parsed.get("gen_status", "")).lower()
        if gen_status in {"failed", "fail", "error"}:
            raise JimengCliError(
                "generation_failed",
                f"dreamina generation failed (gen_status={gen_status})",
                stderr=err[:500],
            )

        if not submit_id and rc != 0:
            raise JimengCliError(
                "generation_failed",
                f"dreamina {submit_args[0]} exited {rc} with no submit_id",
                stderr=err[:500],
            )
        if not submit_id:
            raise JimengCliError(
                "parse_error",
                f"could not extract submit_id from dreamina {submit_args[0]} output",
                stderr=err[:500],
            )

        # Fetch the product into download_dir (idempotent: the submit may already
        # have downloaded, but query_result guarantees the file is local).
        q_rc, q_out, q_err = await self._run_cli(
            [
                "query_result",
                f"--submit_id={submit_id}",
                f"--download_dir={download_dir}",
            ],
            self._query_timeout,
        )

        local_path = _first_media_file(download_dir, exts)
        if not local_path:
            # No product → only now interrogate the query output for a structured
            # cause (same M1 discipline: classify on the failure branch, not the
            # success one).
            q_failure = self._classify_failure(q_rc, q_out, q_err)
            if q_failure is not None:
                raise q_failure
            raise JimengCliError(
                "generation_failed",
                f"dreamina produced no media file for submit_id={submit_id}",
                stderr=(err + q_err)[:500],
            )
        return GenResult(
            local_path=local_path, mime=_mime_for(local_path, mime), raw=parsed
        )

    # ------------------------------------------------------------------ health --

    async def health(self) -> dict:
        """Wrap ``user_credit`` → ``{ok, credit?, error?}`` (never raises)."""
        try:
            rc, out, err = await self._run_cli(["user_credit"], timeout=30.0)
        except JimengCliError as exc:
            return {"ok": False, "error": exc.code}
        failure = self._classify_failure(rc, out, err)
        if failure is not None:
            return {"ok": False, "error": failure.code}
        if rc != 0:
            return {"ok": False, "error": "health_failed"}
        credit = _extract_credit(out)
        result: dict = {"ok": True}
        if credit is not None:
            result["credit"] = credit
        return result


def _first_media_file(directory: str, exts: Tuple[str, ...]) -> Optional[str]:
    """First file in ``directory`` (sorted) whose extension is in ``exts``."""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return None
    for name in names:
        if name.lower().endswith(exts):
            return os.path.join(directory, name)
    return None


def _mime_for(path: str, default: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".m4v": "video/mp4",
    }
    return mapping.get(ext, default)


def _extract_credit(text: str) -> Optional[int]:
    """Best-effort remaining-credit number from ``user_credit`` output."""
    parsed = _extract_json(text)
    if parsed:
        for key in ("credit", "balance", "remaining", "total_credit"):
            value = parsed.get(key)
            if isinstance(value, (int, float)):
                return int(value)
    return None
