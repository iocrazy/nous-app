"""Tests for CodexCliProvider — all subprocess interaction is faked.

`asyncio.create_subprocess_exec` is monkeypatched; no real `gpt-image-2-skill`
binary is ever touched. The real `asyncio.wait_for` is kept so the hard-timeout
+ kill path is exercised for real (a FakeProc that hangs until killed).

Wire shapes are copied from real CLI output captured 2026-08-17 (gpt-image-2-
skill 0.7.3, `--json` mode): success = `{"ok": true, "output": {"path": ...}}`,
failure = `{"ok": false, "error": {"code": ..., "message": ...}}` with exit 1.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from app.services.media.parsers.video_providers.codex_cli import (
    CodexCliError,
    CodexCliProvider,
)


# ---------------------------------------------------------------------------
# Fake subprocess plumbing
# ---------------------------------------------------------------------------
class FakeProc:
    """Minimal stand-in for asyncio.subprocess.Process."""

    def __init__(
        self, rc=0, stdout=b"", stderr=b"", *, hang=False, on_run=None, argv=None
    ):
        self.returncode = rc
        self._stdout = stdout
        self._stderr = stderr
        self._hang = hang
        self._killed = asyncio.Event()
        self._on_run = on_run
        self._argv = argv
        self._ran = False
        self.kill_count = 0

    async def communicate(self):
        if self._hang and not self._killed.is_set():
            await self._killed.wait()
            return b"", b""
        if not self._ran and self._on_run is not None:
            self._on_run(self._argv)
            self._ran = True
        return self._stdout, self._stderr

    def kill(self):
        self.kill_count += 1
        self._killed.set()


def _out_path(argv) -> str | None:
    for i, tok in enumerate(argv):
        if tok == "--out" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def install_fake_exec(monkeypatch, make_proc, captured=None):
    """Monkeypatch create_subprocess_exec with `make_proc(argv) -> FakeProc`."""
    procs: list[FakeProc] = []

    async def fake_exec(*cmd, stdout=None, stderr=None, **kwargs):
        argv = list(cmd)
        if captured is not None:
            captured.append(argv)
        proc = make_proc(argv)
        procs.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return procs


def _success_json(path: str) -> bytes:
    # Real shape (abridged to the keys the provider is allowed to rely on).
    return json.dumps(
        {
            "auth": {"refreshed": False, "source": "auth.json"},
            "command": "images generate",
            "ok": True,
            "output": {
                "bytes": 7,
                "files": [{"bytes": 7, "index": 0, "path": path}],
                "path": path,
            },
            "provider": "codex",
            "request": {"model": "gpt-5.4", "size": "1024x1024"},
            "response": {"image_count": 1, "status": "completed"},
        }
    ).encode()


def _error_json(code: str, message: str) -> bytes:
    return json.dumps(
        {"error": {"code": code, "message": message}, "ok": False}
    ).encode()


def _writes_out_and_succeeds(argv) -> FakeProc:
    def on_run(inner_argv):
        path = _out_path(inner_argv)
        assert path, f"--out missing from argv: {inner_argv}"
        Path(path).write_bytes(b"\x89PNG\r\n")

    return FakeProc(
        rc=0, stdout=_success_json(_out_path(argv) or ""), on_run=on_run, argv=argv
    )


# ---------------------------------------------------------------------------
# generate_image — happy path
# ---------------------------------------------------------------------------
class TestGenerateImage:
    async def test_happy_path_produces_local_png(self, monkeypatch):
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, _writes_out_and_succeeds, captured)
        provider = CodexCliProvider(
            bin_path="gpt-image-2-skill", auth_file="/a/auth.json"
        )

        result = await provider.generate_image(prompt="a red apple", aspect="16:9")

        assert Path(result.local_path).is_file()
        assert result.mime == "image/png"
        argv = captured[0]
        assert argv[0] == "gpt-image-2-skill"
        assert "--json" in argv
        assert ["--provider", "codex"] == argv[
            argv.index("--provider") : argv.index("--provider") + 2
        ]
        assert ["--auth-file", "/a/auth.json"] == argv[
            argv.index("--auth-file") : argv.index("--auth-file") + 2
        ]
        assert "images" in argv and "generate" in argv
        # The user's words come FIRST and survive intact. They are no longer
        # the whole argument: since 2026-08-23 an aspect sentence is appended,
        # because --size turned out not to control the output shape on this
        # provider (see TestAspectRatio for the measurements). Asserting strict
        # equality here would pin the old, broken behaviour.
        sent_prompt = argv[argv.index("--prompt") + 1]
        assert sent_prompt.startswith("a red apple")
        assert argv[argv.index("--size") + 1] == "1536x1024"
        assert argv[argv.index("--format") + 1] == "png"

    async def test_model_version_passed_as_model_flag(self, monkeypatch):
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, _writes_out_and_succeeds, captured)
        provider = CodexCliProvider()

        await provider.generate_image(prompt="p", aspect="1:1", model_version="gpt-5.4")

        argv = captured[0]
        assert argv[argv.index("--model") + 1] == "gpt-5.4"
        assert argv[argv.index("--size") + 1] == "1024x1024"

    @pytest.mark.parametrize(
        ("aspect", "size"),
        [
            ("16:9", "1536x1024"),
            ("21:9", "1536x1024"),
            ("4:3", "1536x1024"),
            ("3:2", "1536x1024"),
            ("1:1", "1024x1024"),
            ("9:16", "1024x1536"),
            ("3:4", "1024x1536"),
            ("2:3", "1024x1536"),
            ("", "1024x1024"),
            ("weird", "1024x1024"),
        ],
    )
    async def test_aspect_maps_to_canonical_size(self, monkeypatch, aspect, size):
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, _writes_out_and_succeeds, captured)
        provider = CodexCliProvider()

        await provider.generate_image(prompt="p", aspect=aspect)

        argv = captured[0]
        assert argv[argv.index("--size") + 1] == size

    async def test_ref_image_switches_to_edit_mode(self, monkeypatch, tmp_path):
        ref = tmp_path / "ref.png"
        ref.write_bytes(b"\x89PNG\r\n")
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, _writes_out_and_succeeds, captured)
        provider = CodexCliProvider()

        await provider.generate_image(prompt="p", aspect="1:1", ref_image_path=str(ref))

        argv = captured[0]
        assert "edit" in argv and "generate" not in argv
        assert argv[argv.index("--ref-image") + 1] == str(ref)

    async def test_auth_file_from_env(self, monkeypatch):
        monkeypatch.setenv("CODEX_AUTH_FILE", "/env/auth.json")
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, _writes_out_and_succeeds, captured)
        provider = CodexCliProvider()

        await provider.generate_image(prompt="p", aspect="1:1")

        argv = captured[0]
        assert argv[argv.index("--auth-file") + 1] == "/env/auth.json"

    async def test_no_auth_file_omits_flag(self, monkeypatch):
        monkeypatch.delenv("CODEX_AUTH_FILE", raising=False)
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, _writes_out_and_succeeds, captured)
        provider = CodexCliProvider()

        await provider.generate_image(prompt="p", aspect="1:1")

        assert "--auth-file" not in captured[0]


# ---------------------------------------------------------------------------
# generate_image — failure classification
# ---------------------------------------------------------------------------
class TestFailureClassification:
    async def test_missing_access_token_is_not_logged_in(self, monkeypatch):
        # Real shape captured with a bad --auth-file (exit 1).
        install_fake_exec(
            monkeypatch,
            lambda argv: FakeProc(
                rc=1,
                stdout=_error_json(
                    "access_token_missing", "Missing access_token in /a/auth.json"
                ),
            ),
        )
        provider = CodexCliProvider()

        with pytest.raises(CodexCliError) as exc_info:
            await provider.generate_image(prompt="p", aspect="1:1")
        assert exc_info.value.code == "not_logged_in"

    async def test_unauthorized_message_is_not_logged_in(self, monkeypatch):
        install_fake_exec(
            monkeypatch,
            lambda argv: FakeProc(
                rc=1, stdout=_error_json("http_error", "401 Unauthorized")
            ),
        )
        provider = CodexCliProvider()

        with pytest.raises(CodexCliError) as exc_info:
            await provider.generate_image(prompt="p", aspect="1:1")
        assert exc_info.value.code == "not_logged_in"

    async def test_usage_limit_is_no_credit(self, monkeypatch):
        install_fake_exec(
            monkeypatch,
            lambda argv: FakeProc(
                rc=1,
                stdout=_error_json(
                    "usage_limit_reached", "You've hit your usage limit."
                ),
            ),
        )
        provider = CodexCliProvider()

        with pytest.raises(CodexCliError) as exc_info:
            await provider.generate_image(prompt="p", aspect="1:1")
        assert exc_info.value.code == "no_credit"

    async def test_other_error_is_generation_failed(self, monkeypatch):
        install_fake_exec(
            monkeypatch,
            lambda argv: FakeProc(
                rc=1,
                stdout=_error_json("http_error", "500 Internal Server Error"),
                stderr=b"boom",
            ),
        )
        provider = CodexCliProvider()

        with pytest.raises(CodexCliError) as exc_info:
            await provider.generate_image(prompt="p", aspect="1:1")
        assert exc_info.value.code == "generation_failed"
        assert "boom" in exc_info.value.stderr

    async def test_ok_true_but_no_file_is_generation_failed(self, monkeypatch):
        # ok:true whose output.path was never written — trust the filesystem,
        # not the CLI's claim.
        install_fake_exec(
            monkeypatch,
            lambda argv: FakeProc(rc=0, stdout=_success_json("/nonexistent/gen.png")),
        )
        provider = CodexCliProvider()

        with pytest.raises(CodexCliError) as exc_info:
            await provider.generate_image(prompt="p", aspect="1:1")
        assert exc_info.value.code == "generation_failed"

    async def test_unparseable_stdout_is_parse_error(self, monkeypatch):
        install_fake_exec(
            monkeypatch, lambda argv: FakeProc(rc=0, stdout=b"not json at all")
        )
        provider = CodexCliProvider()

        with pytest.raises(CodexCliError) as exc_info:
            await provider.generate_image(prompt="p", aspect="1:1")
        assert exc_info.value.code == "parse_error"

    async def test_success_payload_with_401ish_token_not_misread(self, monkeypatch):
        # M1 discipline: a *successful* run whose ids happen to contain "401"
        # must never be classified as not_logged_in.
        def make_proc(argv):
            def on_run(inner_argv):
                path = _out_path(inner_argv)
                Path(path).write_bytes(b"\x89PNG\r\n")

            payload = json.loads(_success_json(_out_path(argv) or ""))
            payload["response"]["response_id"] = "resp_401unauthorized401"
            return FakeProc(
                rc=0, stdout=json.dumps(payload).encode(), on_run=on_run, argv=argv
            )

        install_fake_exec(monkeypatch, make_proc)
        provider = CodexCliProvider()

        result = await provider.generate_image(prompt="p", aspect="1:1")
        assert Path(result.local_path).is_file()


# ---------------------------------------------------------------------------
# subprocess discipline
# ---------------------------------------------------------------------------
class TestSubprocessDiscipline:
    async def test_timeout_kills_process(self, monkeypatch):
        procs = install_fake_exec(monkeypatch, lambda argv: FakeProc(hang=True))
        provider = CodexCliProvider(timeout=0.05)

        with pytest.raises(CodexCliError) as exc_info:
            await provider.generate_image(prompt="p", aspect="1:1")
        assert exc_info.value.code == "timeout"
        assert procs[0].kill_count == 1

    async def test_missing_binary_is_cli_missing(self, monkeypatch):
        async def fake_exec(*cmd, **kwargs):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        provider = CodexCliProvider()

        with pytest.raises(CodexCliError) as exc_info:
            await provider.generate_image(prompt="p", aspect="1:1")
        assert exc_info.value.code == "cli_missing"


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
class TestHealth:
    async def test_health_ok_when_doctor_reports_token(self, monkeypatch):
        doctor = {
            "command": "doctor",
            "ok": True,
            "providers": {
                "codex": {
                    "auth": {"access_token_present": True, "auth_mode": "chatgpt"}
                }
            },
        }
        captured: list[list[str]] = []
        install_fake_exec(
            monkeypatch,
            lambda argv: FakeProc(rc=0, stdout=json.dumps(doctor).encode()),
            captured,
        )
        provider = CodexCliProvider()

        assert await provider.health() == {"ok": True}
        assert "doctor" in captured[0]

    async def test_health_not_logged_in_without_token(self, monkeypatch):
        doctor = {
            "command": "doctor",
            "ok": True,
            "providers": {"codex": {"auth": {"access_token_present": False}}},
        }
        install_fake_exec(
            monkeypatch, lambda argv: FakeProc(rc=0, stdout=json.dumps(doctor).encode())
        )
        provider = CodexCliProvider()

        assert await provider.health() == {"ok": False, "error": "not_logged_in"}

    async def test_health_never_raises_on_missing_binary(self, monkeypatch):
        async def fake_exec(*cmd, **kwargs):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        provider = CodexCliProvider()

        assert await provider.health() == {"ok": False, "error": "cli_missing"}


class TestMultiRef:
    async def test_multiple_ref_images_all_reach_the_cli(self, monkeypatch, tmp_path):
        refs = []
        for name in ("r1.png", "r2.png"):
            p = tmp_path / name
            p.write_bytes(b"\x89PNG\r\n")
            refs.append(str(p))
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, _writes_out_and_succeeds, captured)
        provider = CodexCliProvider()

        await provider.generate_image(prompt="p", aspect="1:1", ref_image_paths=refs)

        argv = captured[0]
        assert "edit" in argv
        ref_positions = [i for i, a in enumerate(argv) if a == "--ref-image"]
        assert [argv[i + 1] for i in ref_positions] == refs

    async def test_single_legacy_ref_still_works(self, monkeypatch, tmp_path):
        ref = tmp_path / "solo.png"
        ref.write_bytes(b"\x89PNG\r\n")
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, _writes_out_and_succeeds, captured)
        provider = CodexCliProvider()

        await provider.generate_image(
            prompt="p", aspect="1:1", ref_image_paths=[str(ref)]
        )
        assert "--ref-image" in captured[0]


async def test_generate_image_forces_opaque_background(monkeypatch):
    """--background auto lets the codex chain pick transparent, which renders
    on a pure-green (#00ff00) matte and leaks green spill into the output —
    the 2026-08-20 "all codex images look green" incident. Canvas images are
    full frames: force opaque."""
    captured: list = []
    install_fake_exec(monkeypatch, _writes_out_and_succeeds, captured)
    provider = CodexCliProvider()
    await provider.generate_image(prompt="a red apple", aspect="1:1")
    assert "--background" in captured[0]
    assert captured[0][captured[0].index("--background") + 1] == "opaque"


# ---------------------------------------------------------------------------
# Aspect ratio: the prompt carries it, and the output is checked
#
# ⚠️ This whole block exists because the OPPOSITE was believed and shipped.
# A comment above ``_ASPECT_TO_SIZE`` used to assert "The model supports exactly
# three sizes… every catalog aspect maps to the nearest", so ``--size`` was
# treated as the control. Measured 2026-08-23 against the real CLI:
#
#     --size 999x999                   → "must use ... multiples of 16"
#                                         (not "three sizes" — the comment was
#                                          simply false)
#     --size 1024x1536 + neutral text  → ok:true, produced 1536x1024 LANDSCAPE
#     --size 1024x1536 + portrait text → produced 1086x1448 = exactly 0.7500
#
# and all four codex images in production came back at a ratio nobody asked
# for, three of them flipped to portrait after requesting 16:9 — every one with
# ok:true. So: say the shape in words, then verify what came back.
# ---------------------------------------------------------------------------
class TestAspectRatio:
    def _png(self, path, width: int, height: int) -> None:
        from PIL import Image

        Image.new("RGB", (width, height), (10, 10, 10)).save(path)

    def _exec_writing(self, width: int, height: int):
        """A fake CLI that writes a real PNG of the given size to --out."""

        def make(argv):
            out = _out_path(argv)
            self._png(out, width, height)
            return FakeProc(rc=0, stdout=_success_json(out), argv=argv)

        return make

    async def test_the_aspect_is_stated_in_the_prompt(self, monkeypatch, tmp_path):
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, self._exec_writing(768, 1024), captured)

        await CodexCliProvider().generate_image(prompt="a cat", aspect="3:4")

        argv = captured[0]
        sent = argv[argv.index("--prompt") + 1]
        # The user's words survive, first and intact.
        assert sent.startswith("a cat")
        # And the shape is spelled out, because --size does not control it.
        assert "3:4" in sent and "portrait" in sent

    async def test_an_empty_aspect_adds_nothing_to_the_prompt(
        self, monkeypatch, tmp_path
    ):
        """IC 自适应 sends an empty aspect on purpose. Inventing a shape for
        "you choose" would be worse than saying nothing."""
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, self._exec_writing(1024, 1024), captured)

        await CodexCliProvider().generate_image(prompt="a cat", aspect="")

        argv = captured[0]
        assert argv[argv.index("--prompt") + 1] == "a cat"

    async def test_an_unknown_aspect_adds_nothing_to_the_prompt(
        self, monkeypatch, tmp_path
    ):
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, self._exec_writing(1024, 1024), captured)

        await CodexCliProvider().generate_image(prompt="a cat", aspect="7:13")

        argv = captured[0]
        assert argv[argv.index("--prompt") + 1] == "a cat"

    async def test_size_is_still_sent(self, monkeypatch, tmp_path):
        """Kept deliberately: free to send, it is the CLI's documented
        contract, and an upstream fix would start working with no change."""
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, self._exec_writing(768, 1024), captured)

        await CodexCliProvider().generate_image(prompt="p", aspect="3:4")

        argv = captured[0]
        assert argv[argv.index("--size") + 1] == "1024x1536"

    async def test_a_complying_result_is_marked_honored(self, monkeypatch, tmp_path):
        # 1086x1448 — the real measurement from the 2026-08-23 probe.
        install_fake_exec(monkeypatch, self._exec_writing(1086, 1448))

        res = await CodexCliProvider().generate_image(prompt="p", aspect="3:4")

        assert res.raw["measured_size"] == "1086x1448"
        assert res.raw["requested_aspect"] == "3:4"
        assert res.raw["aspect_honored"] is True

    async def test_a_flipped_result_is_marked_NOT_honored(self, monkeypatch, tmp_path):
        """The exact production failure: ask for 16:9 landscape, get portrait.
        It must be recorded as non-compliant — but still returned, because the
        image is already generated and already paid for."""
        install_fake_exec(monkeypatch, self._exec_writing(1184, 1328))

        res = await CodexCliProvider().generate_image(prompt="p", aspect="16:9")

        assert res.raw["aspect_honored"] is False
        assert res.raw["measured_size"] == "1184x1328"
        # Returned, not thrown away.
        assert res.local_path and os.path.isfile(res.local_path)

    async def test_rounding_slack_does_not_count_as_a_violation(
        self, monkeypatch, tmp_path
    ):
        """The model picks its own pixels (1086x1448, not a round 1024x1365),
        so an exact match is not the bar."""
        install_fake_exec(monkeypatch, self._exec_writing(1024, 1365))

        res = await CodexCliProvider().generate_image(prompt="p", aspect="3:4")

        assert res.raw["aspect_honored"] is True

    async def test_an_unmeasurable_file_is_not_treated_as_a_violation(
        self, monkeypatch, tmp_path
    ):
        """Verification is a REPORT, not a gate. Refusing an image we already
        paid for because Pillow could not open it trades a cosmetic problem for
        a real one."""

        def make(argv):
            out = _out_path(argv)
            Path(out).write_bytes(b"not a png")
            return FakeProc(rc=0, stdout=_success_json(out), argv=argv)

        install_fake_exec(monkeypatch, make)

        res = await CodexCliProvider().generate_image(prompt="p", aspect="3:4")

        assert res.local_path
        assert "aspect_honored" not in res.raw
        assert "measured_size" not in res.raw
