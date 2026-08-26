"""Child processes do not inherit our secrets.

Every third-party binary this backend spawns — ffmpeg, ffprobe, yt-dlp, node
(a_bogus signing), dreamina — inherited the parent's complete environment.
Production container, 2026-08-26, that environment holds nine credential-
bearing variables: SUPABASE_SERVICE_ROLE_KEY, MEDIAHUB_TOKEN_ENCRYPTION_KEY,
BROWSER_INTERNAL_TOKEN, SUPABASE_ANON_KEY, DBOS_DATABASE_URL,
SUPAVISOR_DATABASE_URL, NGINX_SECURE_LINK_SECRET, MEDIA_TOKEN_SECRET, GPG_KEY.
yt-dlp, which processes attacker-controlled URLs, could read every one of
them. A crash dump, an `env` in a postprocessor hook, a malicious extractor
plugin — any of those is the whole key ring.

`safe_popen_kwargs()` is already the chokepoint 45 spawn sites splat into, so
the scrubbed env rides it and every site is covered without touching them.
The one site that also passed its own `env=` would collide (TypeError on the
duplicate kwarg); a source scan below keeps that class from coming back.

The exception is our OWN Python child (`services/workforce/isolated_runner`):
it runs our code and needs the database, so it keeps the full environment on
purpose and does not use this chokepoint.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agent_framework.process_lifecycle import (
    SAFE_ENV_NAMES,
    safe_popen_kwargs,
    scrubbed_env,
)

SECRETS = {
    "SUPABASE_SERVICE_ROLE_KEY": "sb_secret_x",
    "MEDIAHUB_TOKEN_ENCRYPTION_KEY": "k",
    "BROWSER_INTERNAL_TOKEN": "t",
    "NGINX_SECURE_LINK_SECRET": "s",
    "MEDIA_TOKEN_SECRET": "s",
    "GPG_KEY": "g",
    "SOME_PASSWORD": "p",
    "DB_PASSWD": "p",
    "AWS_CREDENTIALS": "c",
    "PG_DSN": "postgres://u:p@h/db",
}
HARMLESS = {
    "PATH": "/usr/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "CHROME_PATH": "/usr/bin/chromium",
    "COOKIES_DIR": "/data/cookies",
    "SUPABASE_URL": "http://kong:8000",
    "CODEX_AUTH_FILE": "/run/codex/auth.json",  # a PATH to a secret, not one
    "HTTPS_PROXY": "http://127.0.0.1:7890",
}


@pytest.fixture
def env(monkeypatch):
    for k in list(SECRETS) + list(HARMLESS):
        monkeypatch.delenv(k, raising=False)
    for k, v in {**SECRETS, **HARMLESS}.items():
        monkeypatch.setenv(k, v)


@pytest.mark.unit
def test_every_credential_shaped_name_is_dropped(env):
    out = scrubbed_env()
    leaked = sorted(k for k in SECRETS if k in out)
    assert not leaked, f"secrets reached the child env: {leaked}"


@pytest.mark.unit
def test_harmless_names_survive(env):
    """Over-scrubbing is its own outage: no PATH means no ffmpeg at all."""
    out = scrubbed_env()
    missing = sorted(k for k in HARMLESS if k not in out)
    assert not missing, f"needed vars scrubbed: {missing}"


@pytest.mark.unit
def test_credential_bearing_urls_are_dropped_by_value(env, monkeypatch):
    """`REDIS_URL` matches no name pattern, but `redis://:hunter2@host` is a
    secret regardless of what it is called."""
    monkeypatch.setenv("REDIS_URL", "redis://:hunter2@redis:6379/0")
    monkeypatch.setenv("PLAIN_URL", "http://kong:8000/rest")
    out = scrubbed_env()
    assert "REDIS_URL" not in out
    assert out["PLAIN_URL"] == "http://kong:8000/rest"


@pytest.mark.unit
def test_database_url_names_are_dropped(env, monkeypatch):
    monkeypatch.setenv("DBOS_DATABASE_URL", "postgres://x:y@nous-db:55434/postgres")
    assert "DBOS_DATABASE_URL" not in scrubbed_env()


@pytest.mark.unit
def test_known_benign_matches_are_kept(env, monkeypatch):
    """`TOKENIZERS_PARALLELISM` matches *TOKEN* and is a harmless HF switch —
    the explicit safe list is how a false positive is answered, not by
    loosening the pattern."""
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    assert "TOKENIZERS_PARALLELISM" in SAFE_ENV_NAMES
    assert scrubbed_env()["TOKENIZERS_PARALLELISM"] == "false"


@pytest.mark.unit
def test_keep_reinstates_a_named_secret_for_a_child_that_needs_it(env):
    """The escape hatch is explicit and per-call: a site that truly needs one
    credential names it, and only that one comes back."""
    out = scrubbed_env(keep=("BROWSER_INTERNAL_TOKEN",))
    assert out["BROWSER_INTERNAL_TOKEN"] == "t"
    assert "SUPABASE_SERVICE_ROLE_KEY" not in out


@pytest.mark.unit
def test_extra_adds_child_specific_vars(env):
    out = scrubbed_env(extra={"DOUYIN_UA": "Mozilla/5.0"})
    assert out["DOUYIN_UA"] == "Mozilla/5.0"


@pytest.mark.unit
def test_safe_popen_kwargs_carries_the_scrubbed_env(env):
    """The chokepoint — 45 spawn sites splat this. If env is not in it, the
    scrub exists but nobody gets it."""
    kw = safe_popen_kwargs()
    assert "env" in kw
    assert "SUPABASE_SERVICE_ROLE_KEY" not in kw["env"]
    assert kw["env"]["PATH"] == "/usr/bin"


@pytest.mark.unit
def test_safe_popen_kwargs_threads_keep_and_extra(env):
    kw = safe_popen_kwargs(env_keep=("GPG_KEY",), env_extra={"X": "1"})
    assert kw["env"]["GPG_KEY"] == "g" and kw["env"]["X"] == "1"


@pytest.mark.unit
def test_the_result_is_a_fresh_dict_not_os_environ(env):
    """Mutating the child's env must never write back into our own process."""
    import os

    out = scrubbed_env()
    out["INJECTED"] = "1"
    assert "INJECTED" not in os.environ


# ── the collision guard ───────────────────────────────────────────────────


def _spawn_sites_with_explicit_env(root: Path):
    """Sites that pass their own `env=` while also splatting
    safe_popen_kwargs() — the duplicate kwarg is a TypeError at call time,
    and the only way to notice is when that code path runs in production."""
    hits = []
    for path in root.rglob("*.py"):
        if "/tests/" in str(path):
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        if "safe_popen_kwargs" not in src:
            continue
        for m in re.finditer(r"safe_popen_kwargs\(", src):
            # look back over the enclosing call for an explicit env=
            window = src[max(0, m.start() - 600) : m.start()]
            tail = window[
                (
                    window.rfind("subprocess_exec(")
                    if "subprocess_exec(" in window
                    else window.rfind("subprocess.")
                ) :
            ]
            if re.search(r"^\s*env\s*=", tail, re.M):
                hits.append(f"{path}:{src[: m.start()].count(chr(10)) + 1}")
    return hits


@pytest.mark.unit
def test_no_spawn_site_passes_env_and_safe_popen_kwargs_together():
    root = Path(__file__).resolve().parents[2] / "app"
    assert root.exists()
    hits = _spawn_sites_with_explicit_env(root)
    assert not hits, (
        "these pass env= AND splat safe_popen_kwargs() — duplicate kwarg, "
        "TypeError at spawn; use safe_popen_kwargs(env_extra=...) instead:\n  "
        + "\n  ".join(hits)
    )


@pytest.mark.unit
def test_the_collision_scanner_can_see_the_defect(tmp_path):
    """Both scans pass by finding nothing — prove this one CAN find it."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text(
        "def f():\n"
        "    completed = subprocess.run(\n"
        "        [node_bin, js],\n"
        "        env={**os.environ, 'X': '1'},\n"
        "        **safe_popen_kwargs(),\n"
        "    )\n"
    )
    assert _spawn_sites_with_explicit_env(tmp_path / "app")


@pytest.mark.unit
def test_our_own_python_child_keeps_the_full_env_on_purpose():
    """isolated_runner runs OUR code and needs the database — it is the one
    documented exception and must not silently start using the chokepoint."""
    src = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "workforce"
        / "isolated_runner.py"
    ).read_text()
    assert "safe_popen_kwargs" not in src
    assert "os.environ.copy()" in src
