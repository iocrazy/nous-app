"""The migrated spawn sites, driven by REAL fake binaries on PATH.

Each fake is a tiny shell script that starts a grandchild (the yt-dlp →
ffmpeg shape) and records both pids. The old sites killed only the direct
child (or nothing at all, or never reaped), so the grandchild survived; the
stubbed-``communicate()`` tests could not see that.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import signal
import stat
import sys
import time
from pathlib import Path

import pytest


def _is_dead(pid: int) -> bool:
    """Not running: gone, or a zombie waiting for its (possibly init) reaper.
    Local on purpose — this file must observe the sites independently of
    kill_tree's own predicate."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    if sys.platform.startswith("linux"):
        try:
            stat_line = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return True
        return stat_line.rsplit(")", 1)[1].split()[0] == "Z"
    import subprocess

    out = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return not out or out.startswith("Z")


pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell fakes"),
]

#: Long enough that the fake has written its pidfiles before the deadline
#: fires even on a loaded machine (0.5 s raced on macOS under load).
SPAWN_TIMEOUT_S = 1.5

# sh script: remember own pid + a sleeping grandchild's pid, then block.
_HANGING = """#!/bin/sh
echo "$$" > "{pidfile}.self"
sleep 60 &
echo "$!" > "{pidfile}.grand"
echo started
wait
"""


def _write_fake(bin_dir: Path, name: str, body: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _install(monkeypatch, tmp_path: Path, name: str) -> Path:
    pidfile = tmp_path / f"{name}.pid"
    _write_fake(tmp_path / "bin", name, _HANGING.format(pidfile=pidfile))
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    return pidfile


def _pids(pidfile: Path) -> tuple[int, int]:
    return (
        int(Path(f"{pidfile}.self").read_text()),
        int(Path(f"{pidfile}.grand").read_text()),
    )


async def _assert_tree_dead(pidfile: Path) -> None:
    me, grand = _pids(pidfile)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not (_is_dead(me) and _is_dead(grand)):
        await asyncio.sleep(0.05)
    assert _is_dead(me), "the direct child survived"
    assert _is_dead(grand), "the grandchild survived the timeout"


# ── ytdlp: three sites ────────────────────────────────────────────────────


@pytest.fixture
def ytdlp(monkeypatch):
    from app.services.media.parsers import ytdlp_service as mod

    monkeypatch.setattr(mod.YtdlpService, "_get_proxy_args", staticmethod(lambda u: []))
    monkeypatch.setattr(
        mod.YtdlpService, "_get_cookie_args", staticmethod(lambda u, user_id=None: [])
    )
    return mod


async def test_ytdlp_audio_timeout_kills_child(monkeypatch, tmp_path, ytdlp):
    from app.boundary import ValidatedURL

    pidfile = _install(monkeypatch, tmp_path, "yt-dlp")
    monkeypatch.setattr(ytdlp, "YTDLP_AUDIO_TIMEOUT_S", SPAWN_TIMEOUT_S, raising=False)
    monkeypatch.setattr(ytdlp, "YTDLP_KILL_GRACE_S", 0.3, raising=False)
    with pytest.raises(RuntimeError, match="timed out"):
        await asyncio.wait_for(
            ytdlp.YtdlpService.download_audio(
                ValidatedURL("https://example.com/v"), str(tmp_path / "o"), "p1"
            ),
            timeout=8,
        )
    await _assert_tree_dead(pidfile)


async def test_ytdlp_download_timeout_bounds_reads(monkeypatch, tmp_path, ytdlp):
    """H6: the 600 s timeout wrapped only proc.wait(), AFTER an unbounded
    read-until-EOF — a hung yt-dlp with open pipes blocked forever."""
    from app.boundary import ValidatedURL

    pidfile = _install(monkeypatch, tmp_path, "yt-dlp")
    monkeypatch.setattr(
        ytdlp, "YTDLP_DOWNLOAD_TIMEOUT_S", SPAWN_TIMEOUT_S, raising=False
    )
    monkeypatch.setattr(ytdlp, "YTDLP_KILL_GRACE_S", 0.3, raising=False)
    with pytest.raises(RuntimeError, match="timed out"):
        await asyncio.wait_for(
            ytdlp.YtdlpService.download_video(
                ValidatedURL("https://example.com/v"), str(tmp_path / "o"), "p1"
            ),
            timeout=8,
        )
    await _assert_tree_dead(pidfile)


async def test_ytdlp_metadata_timeout_kills_tree(monkeypatch, tmp_path, ytdlp):
    from app.boundary import ValidatedURL

    pidfile = _install(monkeypatch, tmp_path, "yt-dlp")
    monkeypatch.setattr(
        ytdlp, "YTDLP_METADATA_TIMEOUT_S", SPAWN_TIMEOUT_S, raising=False
    )
    monkeypatch.setattr(ytdlp, "YTDLP_KILL_GRACE_S", 0.3, raising=False)
    with pytest.raises(RuntimeError, match="timed out"):
        await asyncio.wait_for(
            ytdlp.YtdlpService.fetch_metadata(ValidatedURL("https://example.com/v")),
            timeout=8,
        )
    await _assert_tree_dead(pidfile)


async def test_ytdlp_download_streams_progress(monkeypatch, tmp_path, ytdlp):
    from app.boundary import ValidatedURL

    out_dir = tmp_path / "o"
    body = f"""#!/bin/sh
echo "download:50.0% 5 10 1MiB/s"
echo "download:100.0% 10 10 1MiB/s"
mkdir -p "{out_dir}"
printf x > "{out_dir}/video.mp4"
"""
    _write_fake(tmp_path / "bin", "yt-dlp", body)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    seen = []
    res = await ytdlp.YtdlpService.download_video(
        ValidatedURL("https://example.com/v"),
        str(out_dir),
        "p1",
        progress_callback=lambda d, t, s: seen.append((d, t, s)),
    )
    assert seen == [(5, 10, "1MiB/s"), (10, 10, "1MiB/s")]
    assert res["file_size"] == 1


# ── shot_frames / canvas_timeline / jimeng router ────────────────────────


async def test_shot_frames_timeout_kills_tree(monkeypatch, tmp_path):
    from app.services.library import shot_frames

    pidfile = _install(monkeypatch, tmp_path, "ffmpeg")
    with pytest.raises(shot_frames.ShotFramesError) as exc:
        await asyncio.wait_for(
            shot_frames.sample_frames(
                "in.mp4", tmp_path, fps=1.0, timeout_seconds=SPAWN_TIMEOUT_S
            ),
            timeout=8,
        )
    assert exc.value.reason == "timeout"
    await _assert_tree_dead(pidfile)


async def test_canvas_timeline_ffmpeg_has_a_timeout(monkeypatch, tmp_path):
    """_run_ffmpeg had no timeout at all."""
    from app.workflows import canvas_timeline

    pidfile = _install(monkeypatch, tmp_path, "ffmpeg")
    monkeypatch.setattr(
        canvas_timeline, "FFMPEG_TIMEOUT_S", SPAWN_TIMEOUT_S, raising=False
    )
    with pytest.raises(RuntimeError, match="timed out"):
        await asyncio.wait_for(canvas_timeline._run_ffmpeg(["ffmpeg", "-y"]), timeout=8)
    await _assert_tree_dead(pidfile)


async def test_jimeng_router_run_dreamina_timeout_kills_and_reaps(
    monkeypatch, tmp_path
):
    r = importlib.import_module("app.api.jimeng_cli_router")

    pidfile = _install(monkeypatch, tmp_path, "dreamina")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            r._run_dreamina(["user_credit"], timeout_s=SPAWN_TIMEOUT_S), timeout=8
        )
    await _assert_tree_dead(pidfile)


async def test_jimeng_router_signal_death_is_not_rc_zero(monkeypatch, tmp_path):
    """``proc.returncode or 0`` turned a signal death into success-shaped 0."""
    r = importlib.import_module("app.api.jimeng_cli_router")

    _write_fake(tmp_path / "bin", "dreamina", "#!/bin/sh\nkill -TERM $$\n")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    rc, _out, _err = await r._run_dreamina(["user_credit"], timeout_s=5)
    assert rc == -signal.SIGTERM


# ── jimeng / codex providers ──────────────────────────────────────────────


async def test_jimeng_cli_timeout_kills_tree(monkeypatch, tmp_path):
    from app.services.media.parsers.video_providers.jimeng_cli import (
        JimengCliError,
        JimengCliProvider,
    )

    pidfile = tmp_path / "dreamina.pid"
    fake = _write_fake(tmp_path / "bin", "dreamina", _HANGING.format(pidfile=pidfile))
    provider = JimengCliProvider(bin_path=str(fake))
    with pytest.raises(JimengCliError) as exc:
        await asyncio.wait_for(
            provider._run_cli(["text2image"], timeout=SPAWN_TIMEOUT_S), timeout=8
        )
    assert exc.value.code == "timeout"
    await _assert_tree_dead(pidfile)


async def test_codex_cli_timeout_kills_tree(monkeypatch, tmp_path):
    from app.services.media.parsers.video_providers.codex_cli import (
        CodexCliError,
        CodexCliProvider,
    )

    pidfile = tmp_path / "skill.pid"
    fake = _write_fake(tmp_path / "bin", "skill", _HANGING.format(pidfile=pidfile))
    provider = CodexCliProvider(bin_path=str(fake), timeout=SPAWN_TIMEOUT_S)
    with pytest.raises(CodexCliError) as exc:
        await asyncio.wait_for(
            provider._run_cli(["generate"], timeout=SPAWN_TIMEOUT_S), timeout=8
        )
    assert exc.value.code == "timeout"
    await _assert_tree_dead(pidfile)


# ── isolated_runner (sync, keeps the full env on purpose) ─────────────────


def _py_fake(tmp_path: Path, body: str) -> str:
    """A fake interpreter: ignores ``-m app.run_isolated`` and runs ``body``."""
    return str(_write_fake(tmp_path / "bin", "fakepy", "#!/bin/sh\n" + body))


def test_isolated_runner_signal_death_maps_segfault_and_sets_signal(tmp_path):
    """H4: subprocess reports a signal death as rc=-11; the 139 map never
    matched and a real segfault fell through to runtime_error."""
    from app.services.workforce.isolated_runner import run_isolated

    exe = _py_fake(tmp_path, "cat >/dev/null\nkill -SEGV $$\n")
    res = run_isolated({"id": "t1"}, timeout_s=10, python_exe=exe)
    assert res.error_code == "segfault"
    assert res.signal == signal.SIGSEGV
    assert res.exit_code is None
    assert res.timed_out is False


def test_isolated_runner_literal_exit_137_is_not_oom(tmp_path):
    from app.services.workforce.isolated_runner import run_isolated

    exe = _py_fake(tmp_path, "cat >/dev/null\nexit 137\n")
    res = run_isolated({"id": "t1"}, timeout_s=10, python_exe=exe)
    assert res.exit_code == 137
    assert res.signal is None
    assert res.error_code == "runtime_error"


def test_isolated_runner_timeout_sets_timed_out_keeps_rc_and_envelope(tmp_path):
    """The synthesised 124 is gone; the real reaped status and the partial
    envelope the child already wrote both survive the timeout."""
    from app.services.workforce.isolated_runner import run_isolated

    envelope = json.dumps({"status": "done", "run_id": "run-partial", "task_id": "t1"})
    pidfile = tmp_path / "iso.pid"
    exe = _py_fake(
        tmp_path,
        "cat >/dev/null\n"
        f"echo '{envelope}'\n"
        f'sleep 60 &\necho "$!" > "{pidfile}.grand"\necho "$$" > "{pidfile}.self"\n'
        "wait\n",
    )
    t0 = time.monotonic()
    res = run_isolated({"id": "t1"}, timeout_s=0.8, python_exe=exe)
    assert time.monotonic() - t0 < 8
    assert res.timed_out is True
    assert res.error_code == "timeout"
    assert res.status == "failed"
    assert res.run_id == "run-partial"
    assert res.exit_code != 124
    assert res.signal in (signal.SIGTERM, signal.SIGKILL)
    me, grand = _pids(pidfile)
    assert _is_dead(me) and _is_dead(grand)


def test_isolated_runner_still_keeps_full_env(tmp_path, monkeypatch):
    """The documented exception survives the rewrite: our own Python child
    still sees the database credentials."""
    from app.services.workforce.isolated_runner import run_isolated

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_kept")
    exe = _py_fake(
        tmp_path,
        "cat >/dev/null\n"
        'printf \'{"status":"done","run_id":"%s"}\\n\' "$SUPABASE_SERVICE_ROLE_KEY"\n',
    )
    res = run_isolated({"id": "t1"}, timeout_s=10, python_exe=exe)
    assert res.run_id == "sb_secret_kept"
    assert res.status == "done"
