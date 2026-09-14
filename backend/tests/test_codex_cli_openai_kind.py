"""CodexCliProvider with ``provider_kind="openai"`` — the API-key path.

Same binary, a different provider behind it: ``--provider openai`` talks to the
public OpenAI Images API, which HONOURS ``--size`` (the codex subscription path
does not — see the measurement block in ``codex_cli.py``). So this kind sends
the exact (ratio, resolution) pixels from ``image_size_for`` and does NOT append
the prose aspect sentence, while the ``codex`` kind keeps both behaviours
byte-for-byte.

The key rides in the child's environment, never on argv: the process table is
world-readable. ``test_api_key_never_reaches_argv_source_guard`` is the standing
proof that nobody reintroduces a ``--api-key`` flag.

Subprocess plumbing is faked (no binary is ever run). Unlike the stub in
``test_codex_cli_provider.py`` this one also records the spawn **kwargs**, which
is where the env assertion lives.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services.media.parsers.video_providers.codex_cli import CodexCliProvider


class _Stub:
    """Records every spawn as ``(cmd, kwargs)`` and fakes one CLI run."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        # Replaced per-test for the doctor/health cases.
        self.stdout: bytes | None = None
        self.returncode = 0

    @property
    def last(self) -> tuple[list[str], dict]:
        assert self.calls, "the CLI was never invoked"
        return self.calls[-1]

    def _payload(self, argv: list[str]) -> bytes:
        if self.stdout is not None:
            return self.stdout
        out = ""
        for i, tok in enumerate(argv):
            if tok == "--out" and i + 1 < len(argv):
                out = argv[i + 1]
        # The CLI writes the PNG itself; the provider trusts the filesystem,
        # not the envelope, so the file has to exist.
        if out:
            Path(out).write_bytes(b"\x89PNG\r\n")
        return json.dumps({"ok": True, "output": {"path": out}}).encode()


class _FakeProc:
    def __init__(self, rc: int, stdout: bytes) -> None:
        self.returncode = rc
        self._stdout = stdout

    async def communicate(self):
        return self._stdout, b""

    def kill(self):  # pragma: no cover — the timeout path is not exercised here
        pass


@pytest.fixture
def cli_stub(monkeypatch):
    stub = _Stub()

    async def fake_exec(*cmd, stdout=None, stderr=None, **kwargs):
        argv = list(cmd)
        stub.calls.append((argv, kwargs))
        return _FakeProc(stub.returncode, stub._payload(argv))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return stub


# --------------------------------------------------------------------- argv --


@pytest.mark.unit
async def test_openai_kind_uses_openai_provider_and_env_key(cli_stub):
    p = CodexCliProvider(provider_kind="openai", api_key="sk-test-123", bin_path="gis")

    await p.generate_image(
        prompt="a fox",
        aspect="16:9",
        model_version="gpt-image-2.5-flare",
        quality="xhigh",
        resolution="2k",
    )

    cmd, kwargs = cli_stub.last
    assert cmd[:5] == ["gis", "--json", "--json-events", "--provider", "openai"]
    assert "--auth-file" not in cmd
    assert "--api-key" not in cmd and "sk-test-123" not in " ".join(cmd)
    assert kwargs["env"]["OPENAI_API_KEY"] == "sk-test-123"
    assert cmd[cmd.index("--size") + 1] == "2560x1440"
    assert cmd[cmd.index("--quality") + 1] == "xhigh"
    assert cmd[cmd.index("--model") + 1] == "gpt-image-2.5-flare"
    assert cmd[cmd.index("--background") + 1] == "opaque"
    prompt = cmd[cmd.index("--prompt") + 1]
    assert prompt == "a fox"  # no prose aspect hint on the API path


@pytest.mark.unit
async def test_openai_kind_ignores_auth_file_even_when_set(cli_stub):
    """``--auth-file`` is the codex session; on the API path it means nothing."""
    p = CodexCliProvider(
        provider_kind="openai",
        api_key="sk-test-123",
        bin_path="gis",
        auth_file="/x/auth.json",
    )

    await p.generate_image(prompt="a fox", aspect="1:1")

    cmd, _ = cli_stub.last
    assert "--auth-file" not in cmd
    assert "/x/auth.json" not in cmd


@pytest.mark.unit
async def test_openai_kind_unknown_resolution_falls_back_to_1k(cli_stub):
    p = CodexCliProvider(provider_kind="openai", api_key="k", bin_path="gis")

    await p.generate_image(prompt="a fox", aspect="16:9", resolution=None)

    cmd, _ = cli_stub.last
    assert cmd[cmd.index("--size") + 1] == "1536x864"


@pytest.mark.unit
async def test_codex_kind_is_unchanged(cli_stub):
    p = CodexCliProvider(bin_path="gis", auth_file="/x/auth.json")

    await p.generate_image(
        prompt="a fox", aspect="16:9", quality="high", resolution="2k"
    )

    cmd, kwargs = cli_stub.last
    assert cmd[:7] == [
        "gis",
        "--json",
        "--json-events",
        "--provider",
        "codex",
        "--auth-file",
        "/x/auth.json",
    ]
    assert (
        cmd[cmd.index("--size") + 1] == "1536x1024"
    )  # CODEX_SIZES, resolution ignored
    assert cmd[cmd.index("--prompt") + 1].startswith("a fox")
    assert "16:9" in cmd[cmd.index("--prompt") + 1]  # prose hint still appended
    assert "OPENAI_API_KEY" not in (kwargs.get("env") or {})


# ------------------------------------------------------------- construction --


@pytest.mark.unit
def test_openai_kind_requires_a_key():
    with pytest.raises(ValueError):
        CodexCliProvider(provider_kind="openai", api_key="")


@pytest.mark.unit
def test_openai_kind_rejects_a_blank_key():
    with pytest.raises(ValueError):
        CodexCliProvider(provider_kind="openai", api_key="   ")


@pytest.mark.unit
def test_codex_kind_needs_no_key():
    CodexCliProvider(provider_kind="codex")


@pytest.mark.unit
def test_api_key_never_reaches_argv_source_guard():
    import inspect

    from app.services.media.parsers.video_providers import codex_cli

    assert "--api-key" not in inspect.getsource(codex_cli)


# ------------------------------------------------------------------- health --


@pytest.mark.unit
async def test_health_openai_kind_reads_api_key_present(cli_stub):
    cli_stub.stdout = json.dumps(
        {"providers": {"openai": {"auth": {"api_key_present": True}}}}
    ).encode()
    p = CodexCliProvider(provider_kind="openai", api_key="k", bin_path="gis")

    assert await p.health() == {"ok": True}
    cmd, kwargs = cli_stub.last
    assert cmd[:5] == ["gis", "--json", "--json-events", "--provider", "openai"]
    assert kwargs["env"]["OPENAI_API_KEY"] == "k"


@pytest.mark.unit
async def test_health_openai_kind_missing_key_is_typed(cli_stub):
    cli_stub.stdout = json.dumps(
        {"providers": {"openai": {"auth": {"api_key_present": False}}}}
    ).encode()
    p = CodexCliProvider(provider_kind="openai", api_key="k", bin_path="gis")

    assert await p.health() == {"ok": False, "error": "api_key_missing"}


@pytest.mark.unit
async def test_health_codex_kind_still_reads_access_token(cli_stub):
    cli_stub.stdout = json.dumps(
        {"providers": {"codex": {"auth": {"access_token_present": True}}}}
    ).encode()
    p = CodexCliProvider(bin_path="gis")

    assert await p.health() == {"ok": True}
