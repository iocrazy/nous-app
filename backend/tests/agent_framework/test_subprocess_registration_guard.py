"""Every spawn under workflows / media / workforce goes through run_process.

H1 (fh4 recon A): not one production spawn site called
``register_subprocess``, so Task Center cancel and flow cascade cancel killed
nothing — "Cancel killed N" never appeared once in 30 days of logs. A spawn
that bypasses ``run_process`` is invisible to cancel, to teardown and to the
orthogonal timeout report, and nothing fails: the child simply outlives its
workflow. This scan makes that shape a test failure.

AST, not regex (same scanner shape as ``test_scrubbed_env``): comments,
docstrings and ``asyncio.run(`` cannot confuse it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: module → the attributes of it that start a child process.
_SPAWN_FUNCS = {
    "subprocess": {
        "Popen",
        "run",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
    },
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
}

#: Directories whose spawns must ride run_process (relative to app/).
GUARDED_DIRS = ("workflows/", "services/media/", "services/workforce/")

#: Each entry is a deliberate exception, with the reason. A listed file that no
#: longer spawns anything fails ``test_allowlist_has_no_stale_entries`` —
#: migrate it, then delete its line.
ALLOWLIST: dict[str, str] = {
    # Sync by design (runs inside a sync DBOS step) and keeps the full env on
    # purpose (our own Python, needs the DB). Carries the same orthogonal
    # fields itself (timed_out / signal / exit_code on IsolatedRunResult) and
    # kills its group with kill_process_tree_sync.
    "services/workforce/isolated_runner.py": "sync step; own orthogonal result",
    # ── deferred to the follow-up ticket (fh4 ruling 2) ──
    "services/media/transcode/transcode_probe.py": "deferred: ffprobe probes",
    "services/media/transcode/transcode_service.py": "deferred: transcode",
    "services/media/render/thumbnail_service.py": "deferred: thumbnails",
    "services/media/render/video_frame_extractor.py": "deferred: frame extractor",
    # Not in ruling 2's list either: ffmpeg optimize/verify (verify timeout
    # reads as "assume OK" — its own ticket) and sync node a_bogus signing.
    "services/media/downloader/downloader.py": "deferred: ffmpeg optimize/verify",
    "services/media/parsers/douyin_parse/abogus_parser.py": "deferred: sync node sign",
}


def _spawn_name(call: ast.Call, aliases: dict[str, str]) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module, attr = func.value.id, func.attr
        if attr in _SPAWN_FUNCS.get(module, ()):
            return f"{module}.{attr}"
    if isinstance(func, ast.Name) and func.id in aliases:
        return aliases[func.id]
    return None


def _spawns_in(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = {
        (a.asname or a.name): f"{node.module}.{a.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in _SPAWN_FUNCS
        for a in node.names
        if a.name in _SPAWN_FUNCS[node.module]
    }
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _spawn_name(node, aliases)
            if name:
                hits.append((node.lineno, name))
    return sorted(hits)


def _guarded_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(GUARDED_DIRS):
            yield rel, path


def unregistered_spawn_sites(root: Path) -> list[str]:
    hits = []
    for rel, path in _guarded_files(root):
        if rel in ALLOWLIST:
            continue
        for lineno, name in _spawns_in(path):
            hits.append(f"{rel}:{lineno} {name}(...)")
    return hits


APP = Path(__file__).resolve().parents[2] / "app"


@pytest.mark.unit
def test_spawn_sites_register_with_workflow():
    hits = unregistered_spawn_sites(APP)
    assert not hits, (
        "these spawn a child directly instead of through "
        "app.agent_framework.process_runner.run_process — the child is "
        "invisible to cancel and teardown, and its timeout cannot be told "
        "apart from its exit status:\n  " + "\n  ".join(hits)
    )


@pytest.mark.unit
def test_allowlist_has_no_stale_entries():
    stale = [
        rel
        for rel in ALLOWLIST
        if not (APP / rel).exists() or not _spawns_in(APP / rel)
    ]
    assert not stale, f"allowlisted but no longer spawning — delete: {stale}"


@pytest.mark.unit
def test_the_registration_scanner_can_see_the_defect(tmp_path):
    """Passing by finding nothing proves nothing — prove it CAN find each
    shape, respects the allowlist, and ignores unguarded dirs."""
    app = tmp_path / "app"
    (app / "workflows").mkdir(parents=True)
    (app / "services" / "workforce").mkdir(parents=True)
    (app / "api").mkdir(parents=True)
    (app / "workflows" / "bad.py").write_text(
        "import asyncio, subprocess\n"
        "from subprocess import run as r\n"
        "async def f():\n"
        "    await asyncio.create_subprocess_exec('ffmpeg', **safe_popen_kwargs())\n"
        "    r(['x'])\n"
        "    asyncio.run(g())\n"
        "    await run_process(['ok'], timeout_s=1)\n"
    )
    (app / "services" / "workforce" / "isolated_runner.py").write_text(
        "import subprocess\nsubprocess.Popen(['python'])\n"
    )
    (app / "api" / "elsewhere.py").write_text(
        "import subprocess\nsubprocess.run(['x'])\n"
    )
    assert unregistered_spawn_sites(app) == [
        "workflows/bad.py:4 asyncio.create_subprocess_exec(...)",
        "workflows/bad.py:5 subprocess.run(...)",
    ]
