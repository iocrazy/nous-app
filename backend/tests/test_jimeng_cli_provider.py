"""Tests for JimengCliProvider — all subprocess interaction is faked.

`asyncio.create_subprocess_exec` is monkeypatched with a fake that returns a
canned FakeProc per subcommand; no real `dreamina` binary is ever touched. The
real `asyncio.wait_for` is kept so the hard-timeout + kill path is exercised for
real (a FakeProc that hangs until killed).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from app.services.media.parsers.video_providers import jimeng_cli
from app.services.media.parsers.video_providers.jimeng_cli import (
    GenResult,
    JimengCliError,
    JimengCliProvider,
    _extract_json,
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
        # A hanging proc blocks until kill() — models a runaway CLI. wait_for in
        # the provider cancels this; the post-kill call returns immediately.
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


def _download_dir(argv) -> str | None:
    for tok in argv:
        if isinstance(tok, str) and tok.startswith("--download_dir="):
            return tok.split("=", 1)[1]
    return None


def install_fake_exec(monkeypatch, specs, captured=None):
    """Monkeypatch create_subprocess_exec to dispatch by subcommand.

    `specs` maps a subcommand (argv[1]) to a callable(argv) -> FakeProc.
    `captured` (optional list) collects every argv for assertions.
    """
    procs: dict[str, FakeProc] = {}

    async def fake_exec(*cmd, stdout=None, stderr=None, **kwargs):
        argv = list(cmd)
        if captured is not None:
            captured.append(argv)
        subcmd = argv[1] if len(argv) > 1 else ""
        spec = specs[subcmd]
        proc = spec(argv)
        procs[subcmd] = proc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return procs


def _json_bytes(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


def _writes_file(name: str, data: bytes = b"\x89PNG\r\n"):
    """Build an on_run that drops `name` into the argv's --download_dir."""

    def on_run(argv):
        ddir = _download_dir(argv)
        if ddir:
            Path(ddir, name).write_bytes(data)

    return on_run


# ---------------------------------------------------------------------------
# _extract_json — exhaustive
# ---------------------------------------------------------------------------
class TestExtractJson:
    def test_pure_json(self):
        assert _extract_json('{"submit_id": "abc"}') == {"submit_id": "abc"}

    def test_json_wrapped_in_logs(self):
        text = 'INFO starting\n{"submit_id": "x1", "gen_status": "done"}\nINFO bye'
        assert _extract_json(text) == {"submit_id": "x1", "gen_status": "done"}

    def test_prefers_object_with_more_result_keys(self):
        text = '{"note": "hi"} log line {"submit_id": "s", "images": ["a"]}'
        assert _extract_json(text) == {"submit_id": "s", "images": ["a"]}

    def test_nested_object_returns_top_level(self):
        text = 'log {"submit_id": "s", "result_json": {"images": ["a"]}} tail'
        got = _extract_json(text)
        assert got["submit_id"] == "s"
        assert got["result_json"] == {"images": ["a"]}

    def test_no_json_returns_none(self):
        assert _extract_json("just plain logs, no braces here") is None

    def test_empty_returns_none(self):
        assert _extract_json("") is None

    def test_malformed_brace_is_skipped(self):
        # A stray '{' that doesn't parse must not crash; the valid object wins.
        text = 'oops { not json ... {"submit_id": "ok"}'
        assert _extract_json(text) == {"submit_id": "ok"}


# ---------------------------------------------------------------------------
# Generation happy paths
# ---------------------------------------------------------------------------
async def test_generate_image_happy_path(monkeypatch):
    captured: list = []
    install_fake_exec(
        monkeypatch,
        {
            "text2image": lambda argv: FakeProc(
                rc=0,
                stdout=_json_bytes({"submit_id": "img123", "gen_status": "submitted"}),
            ),
            "query_result": lambda argv: FakeProc(
                rc=0,
                stdout=_json_bytes({"gen_status": "done"}),
                on_run=_writes_file("out.png"),
                argv=argv,
            ),
        },
        captured,
    )
    provider = JimengCliProvider()
    result = await provider.generate_image(
        prompt="a cat", aspect="16:9", model_version="5.0"
    )

    assert isinstance(result, GenResult)
    assert result.mime == "image/png"
    assert os.path.isfile(result.local_path)
    assert result.raw["submit_id"] == "img123"
    # Submit argv carried the mapped ratio + model + poll.
    submit_argv = captured[0]
    assert "text2image" in submit_argv
    assert "--ratio=16:9" in submit_argv
    assert "--model_version=5.0" in submit_argv
    assert any(t.startswith("--poll=") for t in submit_argv)
    # query_result fetched by submit_id into a --download_dir.
    query_argv = captured[1]
    assert "--submit_id=img123" in query_argv
    assert any(t.startswith("--download_dir=") for t in query_argv)


async def test_generate_video_text2video(monkeypatch):
    captured: list = []
    install_fake_exec(
        monkeypatch,
        {
            "text2video": lambda argv: FakeProc(
                rc=0, stdout=_json_bytes({"submit_id": "v1"})
            ),
            "query_result": lambda argv: FakeProc(
                rc=0,
                stdout=b"done",
                on_run=_writes_file("clip.mp4", b"\x00\x00\x00 ftyp"),
                argv=argv,
            ),
        },
        captured,
    )
    provider = JimengCliProvider()
    result = await provider.generate_video(
        prompt="a dog", aspect="9:16", model_version="seedance2.0fast"
    )
    assert result.mime == "video/mp4"
    assert result.local_path.endswith("clip.mp4")
    assert "text2video" in captured[0]
    assert "--ratio=9:16" in captured[0]
    assert "--model_version=seedance2.0fast" in captured[0]


async def test_generate_video_image2video_uses_single_image_flag(monkeypatch):
    captured: list = []
    install_fake_exec(
        monkeypatch,
        {
            "image2video": lambda argv: FakeProc(
                rc=0, stdout=_json_bytes({"submit_id": "v2"})
            ),
            "query_result": lambda argv: FakeProc(
                rc=0, stdout=b"", on_run=_writes_file("anim.mp4"), argv=argv
            ),
        },
        captured,
    )
    provider = JimengCliProvider()
    result = await provider.generate_video(
        prompt="camera push", aspect="16:9", image_path="/tmp/first.png"
    )
    assert result.mime == "video/mp4"
    assert "image2video" in captured[0]
    assert "--image=/tmp/first.png" in captured[0]
    # ratio is inferred from the image → not passed.
    assert not any(t.startswith("--ratio=") for t in captured[0])


# ---------------------------------------------------------------------------
# Five failure semantics
# ---------------------------------------------------------------------------
async def test_not_logged_in(monkeypatch):
    # Real CLI output verified 2026-07-07.
    msg = "未检测到有效登录态，请先执行 dreamina login"
    install_fake_exec(
        monkeypatch,
        {"text2image": lambda argv: FakeProc(rc=1, stdout=msg.encode("utf-8"))},
    )
    provider = JimengCliProvider()
    with pytest.raises(JimengCliError) as exc:
        await provider.generate_image(prompt="x", aspect="1:1")
    assert exc.value.code == "not_logged_in"


async def test_no_credit(monkeypatch):
    install_fake_exec(
        monkeypatch,
        {
            "text2image": lambda argv: FakeProc(
                rc=1, stdout="生成失败：额度不足".encode("utf-8")
            )
        },
    )
    provider = JimengCliProvider()
    with pytest.raises(JimengCliError) as exc:
        await provider.generate_image(prompt="x", aspect="1:1")
    assert exc.value.code == "no_credit"


async def test_generation_failed_via_gen_status(monkeypatch):
    install_fake_exec(
        monkeypatch,
        {
            "text2image": lambda argv: FakeProc(
                rc=0, stdout=_json_bytes({"submit_id": "s", "gen_status": "failed"})
            )
        },
    )
    provider = JimengCliProvider()
    with pytest.raises(JimengCliError) as exc:
        await provider.generate_image(prompt="x", aspect="1:1")
    assert exc.value.code == "generation_failed"


async def test_parse_error_when_no_submit_id(monkeypatch):
    install_fake_exec(
        monkeypatch,
        {"text2image": lambda argv: FakeProc(rc=0, stdout=b"all logs, no json, no id")},
    )
    provider = JimengCliProvider()
    with pytest.raises(JimengCliError) as exc:
        await provider.generate_image(prompt="x", aspect="1:1")
    assert exc.value.code == "parse_error"


async def test_generation_failed_when_nonzero_and_no_id(monkeypatch):
    install_fake_exec(
        monkeypatch,
        {
            "text2image": lambda argv: FakeProc(
                rc=2, stdout=b"boom", stderr=b"stack trace"
            )
        },
    )
    provider = JimengCliProvider()
    with pytest.raises(JimengCliError) as exc:
        await provider.generate_image(prompt="x", aspect="1:1")
    assert exc.value.code == "generation_failed"


async def test_no_media_file_produced(monkeypatch):
    # Submit + query both "succeed" but nothing lands in the download dir.
    install_fake_exec(
        monkeypatch,
        {
            "text2image": lambda argv: FakeProc(
                rc=0, stdout=_json_bytes({"submit_id": "s"})
            ),
            "query_result": lambda argv: FakeProc(
                rc=0, stdout=b"done"
            ),  # writes no file
        },
    )
    provider = JimengCliProvider()
    with pytest.raises(JimengCliError) as exc:
        await provider.generate_image(prompt="x", aspect="1:1")
    assert exc.value.code == "generation_failed"


async def test_success_json_containing_401_not_misclassified(monkeypatch):
    # M1: a paid, successful submit whose JSON carries a number like 401 (credit
    # balance, or a submit_id substring) must NEVER be read as not_logged_in. The
    # bare "401" needle is gone and _classify_failure only runs on failure
    # (rc != 0 or no submit_id), so a clean rc=0 + submit_id short-circuits it.
    install_fake_exec(
        monkeypatch,
        {
            "text2image": lambda argv: FakeProc(
                rc=0,
                stdout=_json_bytes(
                    {"submit_id": "ok401", "credit": 401, "gen_status": "submitted"}
                ),
            ),
            "query_result": lambda argv: FakeProc(
                rc=0, stdout=b"done", on_run=_writes_file("out.png"), argv=argv
            ),
        },
    )
    provider = JimengCliProvider()
    result = await provider.generate_image(prompt="x", aspect="1:1")
    assert result.mime == "image/png"
    assert result.raw["submit_id"] == "ok401"


# ---------------------------------------------------------------------------
# Hard timeout + kill path (real wait_for, hanging proc)
# ---------------------------------------------------------------------------
async def test_run_cli_timeout_kills_process(monkeypatch):
    hung = FakeProc(hang=True)
    install_fake_exec(monkeypatch, {"text2image": lambda argv: hung})
    provider = JimengCliProvider()
    with pytest.raises(JimengCliError) as exc:
        await provider._run_cli(["text2image", "--prompt=x"], timeout=0.05)
    assert exc.value.code == "timeout"
    assert hung.kill_count == 1  # the runaway proc was killed


async def test_generate_image_timeout_surfaces(monkeypatch):
    hung = FakeProc(hang=True)
    install_fake_exec(monkeypatch, {"text2image": lambda argv: hung})
    # Tiny budget so the submit hits the hard timeout immediately.
    provider = JimengCliProvider(image_poll=0, image_margin=0)
    with pytest.raises(JimengCliError) as exc:
        await provider.generate_image(prompt="x", aspect="1:1")
    assert exc.value.code == "timeout"
    assert hung.kill_count == 1


async def test_cli_missing_binary(monkeypatch):
    async def boom(*cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    provider = JimengCliProvider(bin_path="/nonexistent/dreamina")
    with pytest.raises(JimengCliError) as exc:
        await provider._run_cli(["user_credit"], timeout=5)
    assert exc.value.code == "cli_missing"


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
async def test_health_not_logged_in(monkeypatch):
    msg = "未检测到有效登录态，请先执行 dreamina login"
    install_fake_exec(
        monkeypatch,
        {"user_credit": lambda argv: FakeProc(rc=1, stdout=msg.encode("utf-8"))},
    )
    provider = JimengCliProvider()
    assert await provider.health() == {"ok": False, "error": "not_logged_in"}


async def test_health_ok_with_credit(monkeypatch):
    install_fake_exec(
        monkeypatch,
        {
            "user_credit": lambda argv: FakeProc(
                rc=0, stdout=_json_bytes({"credit": 4200})
            )
        },
    )
    provider = JimengCliProvider()
    assert await provider.health() == {"ok": True, "credit": 4200}


async def test_health_ok_without_parseable_credit(monkeypatch):
    install_fake_exec(
        monkeypatch,
        {"user_credit": lambda argv: FakeProc(rc=0, stdout=b"credit: plenty")},
    )
    provider = JimengCliProvider()
    assert await provider.health() == {"ok": True}


async def test_health_swallows_cli_error(monkeypatch):
    # health() must never raise — a _run_cli failure becomes {ok: False, error}.
    provider = JimengCliProvider()

    async def raising_run_cli(args, timeout):
        raise JimengCliError("timeout", "boom")

    monkeypatch.setattr(provider, "_run_cli", raising_run_cli)
    assert await provider.health() == {"ok": False, "error": "timeout"}


async def test_generate_video_passes_duration_flag(monkeypatch):
    captured: list = []
    install_fake_exec(
        monkeypatch,
        {
            "text2video": lambda argv: FakeProc(
                rc=0, stdout=_json_bytes({"submit_id": "v3"})
            ),
            "query_result": lambda argv: FakeProc(
                rc=0, stdout=b"", on_run=_writes_file("clip.mp4"), argv=argv
            ),
        },
        captured,
    )
    provider = JimengCliProvider()
    await provider.generate_video(prompt="a cat", aspect="16:9", duration=10)
    assert "--duration=10" in captured[0]


async def test_generate_video_frames2video_with_first_last(monkeypatch):
    captured: list = []
    install_fake_exec(
        monkeypatch,
        {
            "frames2video": lambda argv: FakeProc(
                rc=0, stdout=_json_bytes({"submit_id": "f1"})
            ),
            "query_result": lambda argv: FakeProc(
                rc=0, stdout=b"", on_run=_writes_file("clip.mp4"), argv=argv
            ),
        },
        captured,
    )
    provider = JimengCliProvider()
    await provider.generate_video(
        prompt="morph",
        aspect="16:9",
        first_frame="/tmp/a.png",
        last_frame="/tmp/b.png",
        duration=5,
    )
    assert "frames2video" in captured[0]
    assert "--first=/tmp/a.png" in captured[0]
    assert "--last=/tmp/b.png" in captured[0]


async def test_generate_video_multimodal_with_many_images(monkeypatch):
    captured: list = []
    install_fake_exec(
        monkeypatch,
        {
            "multimodal2video": lambda argv: FakeProc(
                rc=0, stdout=_json_bytes({"submit_id": "m1"})
            ),
            "query_result": lambda argv: FakeProc(
                rc=0, stdout=b"", on_run=_writes_file("clip.mp4"), argv=argv
            ),
        },
        captured,
    )
    provider = JimengCliProvider()
    await provider.generate_video(
        prompt="omni",
        aspect="16:9",
        image_paths=["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"],
    )
    assert "multimodal2video" in captured[0]
    assert "--image=/tmp/a.png" in captured[0]
    assert "--image=/tmp/c.png" in captured[0]


async def test_generate_video_passes_video_resolution(monkeypatch):
    captured: list = []
    install_fake_exec(
        monkeypatch,
        {
            "text2video": lambda argv: FakeProc(
                rc=0, stdout=_json_bytes({"submit_id": "r1"})
            ),
            "query_result": lambda argv: FakeProc(
                rc=0, stdout=b"", on_run=_writes_file("clip.mp4"), argv=argv
            ),
        },
        captured,
    )
    provider = JimengCliProvider()
    await provider.generate_video(prompt="a cat", aspect="16:9", resolution="1080p")
    assert "--video_resolution=1080p" in captured[0]


async def test_generate_video_auto_aspect_omits_ratio(monkeypatch):
    """aspect='auto' (IC 自适应) → no --ratio flag; the CLI infers."""
    captured: list = []
    install_fake_exec(
        monkeypatch,
        {
            "text2video": lambda argv: FakeProc(
                rc=0, stdout=_json_bytes({"submit_id": "a1"})
            ),
            "query_result": lambda argv: FakeProc(
                rc=0, stdout=b"", on_run=_writes_file("clip.mp4"), argv=argv
            ),
        },
        captured,
    )
    provider = JimengCliProvider()
    await provider.generate_video(prompt="free form", aspect="auto")
    assert not any(str(a).startswith("--ratio=") for a in captured[0])


async def test_upscale_image_runs_image_upscale_command(monkeypatch):
    """IC 放大: jimeng CLI `image_upscale --image= --resolution_type=`."""
    captured: list = []
    install_fake_exec(
        monkeypatch,
        {
            "image_upscale": lambda argv: FakeProc(
                rc=0, stdout=_json_bytes({"submit_id": "u1"})
            ),
            "query_result": lambda argv: FakeProc(
                rc=0, stdout=b"", on_run=_writes_file("up.png", b"\x89PNG"), argv=argv
            ),
        },
        captured,
    )
    provider = JimengCliProvider()
    result = await provider.upscale_image(image_path="/tmp/in.png", resolution="4k")
    assert "image_upscale" in captured[0]
    assert "--image=/tmp/in.png" in captured[0]
    assert "--resolution_type=4k" in captured[0]
    assert result.local_path.endswith("up.png")
