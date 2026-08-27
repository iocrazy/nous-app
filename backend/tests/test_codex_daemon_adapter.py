from __future__ import annotations

from uuid import uuid4

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters.codex_daemon import (
    MIN_TEXT_DAEMON_VERSION,
    UNVERSIONED,
    CodexDaemonAdapter,
)
from app.services.codex.daemon_dispatch import DaemonOfflineError
from app.services.codex.errors import CodexLocalError


def _composed(tools=None, model="") -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=uuid4(),
        agent_slug="a",
        model=model,
        temperature=0.7,
        max_tokens=1000,
        system_message="be brief",
        tools=tools or [],
        skill_manifest=[],
        cache_fingerprint="f",
    )


async def _scope(_uid: str) -> int:
    return 42


async def _current_version(_uid: str) -> str:
    """Every test that expects a dispatch declares the daemon version it
    assumes. Explicit on purpose: the real resolver reads Redis presence and
    the devices table, and a test that quietly used it would be asserting
    against whatever the environment happened to answer."""
    return MIN_TEXT_DAEMON_VERSION


@pytest.mark.asyncio
async def test_call_dispatches_text_job_and_returns_openai_shape():
    seen: dict = {}

    async def fake_dispatch(**kw):
        seen.update(kw)
        return {
            "text": "hello",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 4,
                "output_tokens": 2,
            },
        }

    a = CodexDaemonAdapter(
        user_id="u1",
        model="gpt-5",
        dispatch=fake_dispatch,
        scope_resolver=_scope,
        version_resolver=_current_version,
    )
    resp = await a.call(_composed(), [{"role": "user", "content": "hi"}])

    assert seen["user_id"] == "u1" and seen["scope_id"] == 42 and seen["kind"] == "text"
    # The two legs are deliberately unequal: the daemon caps `codex exec` at
    # 180s (payload), the backend waits 210s for the result. The daemon must
    # time out FIRST so its typed code arrives instead of a generic timeout.
    assert seen["timeout_s"] == 210
    assert seen["payload"]["timeout_s"] == 180
    assert seen["payload"]["model"] == "gpt-5"
    assert seen["payload"]["prompt"].startswith("[System]\nbe brief")
    assert seen["payload"]["image_urls"] == []
    assert resp["choices"][0]["message"] == {
        "role": "assistant",
        "content": "hello",
        "tool_calls": [],
    }
    assert resp["choices"][0]["finish_reason"] == "stop"
    assert resp["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
        "prompt_tokens_details": {"cached_tokens": 4},
    }


@pytest.mark.asyncio
async def test_tools_present_is_rejected_before_any_dispatch():
    async def never(**_):
        raise AssertionError("must not dispatch")

    a = CodexDaemonAdapter(
        user_id="u1",
        dispatch=never,
        scope_resolver=_scope,
        version_resolver=_current_version,
    )
    with pytest.raises(CodexLocalError) as ei:
        await a.call(
            _composed(tools=[{"type": "function", "function": {"name": "Skill"}}]), []
        )
    assert ei.value.code == "tools_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raised,code",
    [
        (DaemonOfflineError("no daemon"), "daemon_offline"),
        (TimeoutError("late"), "timeout"),
        (RuntimeError("codex_not_logged_in: run codex login"), "codex_not_logged_in"),
        (RuntimeError("cli_missing: codex not found"), "cli_missing"),
        (RuntimeError("codex_no_output: nothing"), "codex_no_output"),
        (RuntimeError("weird"), "codex_failed"),
        # Already typed: it must pass through untouched. ``CodexLocalError`` is
        # itself a ``RuntimeError``, so without the dedicated except block it
        # would be re-parsed by ``from_daemon_error`` into ``codex_failed``
        # with one marker nested inside another.
        (CodexLocalError("codex_not_logged_in", "x"), "codex_not_logged_in"),
    ],
)
async def test_dispatch_failures_become_typed_codes(raised, code):
    async def boom(**_):
        raise raised

    a = CodexDaemonAdapter(
        user_id="u1",
        dispatch=boom,
        scope_resolver=_scope,
        version_resolver=_current_version,
    )
    with pytest.raises(CodexLocalError) as ei:
        await a.call(_composed(), [{"role": "user", "content": "x"}])
    assert ei.value.code == code


@pytest.mark.asyncio
async def test_images_over_the_backstop_cap_are_trimmed_and_logged():
    """Trimming must never be silent: the flattened prompt still refers to the
    dropped images, so the model would answer about pictures it never got.

    Captured through a loguru sink rather than ``caplog`` because this module
    logs via loguru, which does not propagate into pytest's stdlib capture —
    ``caplog`` would sit empty regardless of whether the warning fired."""
    from loguru import logger

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    seen: dict = {}

    async def fake_dispatch(**kw):
        seen.update(kw)
        return {"text": "ok", "usage": {}}

    try:
        a = CodexDaemonAdapter(
            user_id="u1",
            dispatch=fake_dispatch,
            scope_resolver=_scope,
            version_resolver=_current_version,
        )
        await a.call(
            _composed(),
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe these"},
                        *(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"https://api.nous.ink/{i}.png"},
                            }
                            for i in range(10)
                        ),
                    ],
                }
            ],
        )
    finally:
        logger.remove(sink_id)

    assert len(seen["payload"]["image_urls"]) == 9
    assert seen["payload"]["image_urls"][-1] == "https://api.nous.ink/8.png"
    assert any("dropping 1 of 10 images" in r for r in records), records


def _data_url(decoded_bytes: int) -> str:
    """A base64 data URL whose payload decodes to roughly ``decoded_bytes``."""
    return "data:image/png;base64," + "A" * ((decoded_bytes * 4 + 2) // 3)


def _image_message(*urls: str) -> list[dict]:
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe these"},
                *({"type": "image_url", "image_url": {"url": u}} for u in urls),
            ],
        }
    ]


@pytest.mark.asyncio
async def test_one_small_inline_image_passes_through_untouched():
    seen: dict = {}

    async def fake_dispatch(**kw):
        seen.update(kw)
        return {"text": "ok", "usage": {}}

    url = _data_url(1024 * 1024)
    a = CodexDaemonAdapter(
        user_id="u1",
        dispatch=fake_dispatch,
        scope_resolver=_scope,
        version_resolver=_current_version,
    )
    await a.call(_composed(), _image_message(url, "https://api.nous.ink/x.png"))

    # http(s) URLs are not weighed at all — the daemon streams those itself;
    # only what we inline into the job counts against the budget.
    assert seen["payload"]["image_urls"] == [url, "https://api.nous.ink/x.png"]


@pytest.mark.asyncio
async def test_inline_images_over_the_byte_budget_are_rejected_before_dispatch():
    """The chat layer hands us `data:image/...;base64` URLs, so the job frame
    carries the pixels themselves. Oversized frames have to die HERE: past this
    point the bytes are already in Redis and on the socket, and the failure
    would surface as a transport error with no actionable code."""

    async def never(**_):
        raise AssertionError("must not dispatch")

    a = CodexDaemonAdapter(
        user_id="u1",
        dispatch=never,
        scope_resolver=_scope,
        version_resolver=_current_version,
    )
    with pytest.raises(CodexLocalError) as ei:
        await a.call(
            _composed(),
            _image_message(*(_data_url(1024 * 1024) for _ in range(7))),
        )
    assert ei.value.code == "ref_rejected"
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_row_model_wins_and_the_composed_display_name_is_ignored():
    """``composed.model`` is ``agents.model``, which on this path is the
    catalog row's DISPLAY name ("Codex (Local)") — a label, not a model codex
    can run. If it reached the payload the daemon would shell out
    ``codex exec --model "Codex (Local)"``. Only the row's ``actual_model``
    (passed at construction) may reach the payload; "" means "the user's own
    codex default", which is a legitimate answer for a local CLI."""
    seen: dict = {}

    async def fake_dispatch(**kw):
        seen.update(kw)
        return {"text": "ok", "usage": {}}

    blank = CodexDaemonAdapter(
        user_id="u1",
        model="",
        dispatch=fake_dispatch,
        scope_resolver=_scope,
        version_resolver=_current_version,
    )
    await blank.call(
        _composed(model="Codex (Local)"), [{"role": "user", "content": "x"}]
    )
    assert seen["payload"]["model"] == ""

    pinned = CodexDaemonAdapter(
        user_id="u1",
        model="gpt-5",
        dispatch=fake_dispatch,
        scope_resolver=_scope,
        version_resolver=_current_version,
    )
    await pinned.call(
        _composed(model="Codex (Local)"), [{"role": "user", "content": "x"}]
    )
    assert seen["payload"]["model"] == "gpt-5"
    assert not hasattr(pinned, "stream")


# ── daemon version gate (终审 I-2) ────────────────────────────────────────
#
# The catalog row is global, so every user can pick "Codex (Local)" the day it
# lands — but the daemon on their machine is a script THEY installed. A
# pre-0.3.0 build does not reject a text job: it runs `codex exec --json` with
# no sandbox flags, puts the prompt on argv where `ps` reads it, and returns
# the raw JSONL transcript as the reply. Loud refusal beats all three.


def _gate_adapter(version, dispatch):
    async def resolver(_uid):
        return version

    return CodexDaemonAdapter(
        user_id="u1",
        dispatch=dispatch,
        scope_resolver=_scope,
        version_resolver=resolver,
    )


@pytest.mark.parametrize(
    "reported",
    [
        # A connected daemon whose env_report has no daemon_version at all —
        # the field did not exist before 0.3.0, so this IS the old build.
        UNVERSIONED,
        "0.1.0",
        "0.2.0",
        "0.2.9",
        # Unparseable must read as "too old", never as "cannot tell, allow".
        "not-a-version",
        "",
    ],
)
@pytest.mark.asyncio
async def test_an_outdated_daemon_is_refused_before_anything_is_dispatched(reported):
    async def never(**_kw):  # pragma: no cover - must not run
        raise AssertionError("dispatched to an outdated daemon")

    with pytest.raises(CodexLocalError) as exc:
        await _gate_adapter(reported, never).call(
            _composed(), [{"role": "user", "content": "hi"}]
        )
    assert exc.value.code == "daemon_outdated"
    # 426 Upgrade Required, and 4xx so classify_error calls it non-retryable —
    # an outdated daemon must never silently fall through to a paid model.
    assert exc.value.status_code == 426


@pytest.mark.parametrize("reported", ["0.3.0", "0.3.1", "0.4.0", "1.0.0", "0.3.0-rc1"])
@pytest.mark.asyncio
async def test_a_current_daemon_is_dispatched_to(reported):
    async def fake_dispatch(**_kw):
        return {"text": "ok", "usage": {}}

    resp = await _gate_adapter(reported, fake_dispatch).call(
        _composed(), [{"role": "user", "content": "hi"}]
    )
    assert resp["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_no_connected_daemon_falls_through_to_the_offline_error():
    """``None`` means "nothing connected / could not ask" — NOT a verdict.

    Without this, a user whose daemon simply is not running would be told to
    update it, and updating would not help. Dispatch owns that message.
    """

    async def offline(**_kw):
        raise DaemonOfflineError("not connected")

    with pytest.raises(CodexLocalError) as exc:
        await _gate_adapter(None, offline).call(
            _composed(), [{"role": "user", "content": "hi"}]
        )
    assert exc.value.code == "daemon_offline"


# ── the resolver itself: presence + env_report → a version, or None ───────
#
# The gate above takes a version as given; this is where one actually comes
# from, and where "missing env_report" has to become UNVERSIONED rather than
# None. Getting that backwards would wave every old daemon straight through,
# and the tests above would still be green.


class _FakeRepo:
    def __init__(self, rows):
        self._rows = rows

    async def list_for_user(self, _user_id):
        return self._rows


def _install_resolver_fakes(monkeypatch, *, device_id, rows):
    from app.repositories import codex_daemon_repository as repo_mod
    from app.services.codex import daemon_presence

    async def fake_online(_uid):
        return device_id

    monkeypatch.setattr(daemon_presence, "online_device_id", fake_online)
    monkeypatch.setattr(
        repo_mod, "CodexDaemonRepository", lambda: _FakeRepo(rows), raising=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env_report, expected",
    [
        ({"daemon_version": "0.3.0", "codex_ok": True}, "0.3.0"),
        # Pre-0.3.0 daemons report an env_report WITHOUT the field. This is
        # the case the whole gate exists for.
        ({"codex_ok": True, "node_version": "v20.11.0"}, UNVERSIONED),
        (None, UNVERSIONED),
        ({}, UNVERSIONED),
        ({"daemon_version": ""}, UNVERSIONED),
        ({"daemon_version": 3}, UNVERSIONED),
    ],
)
async def test_resolver_maps_a_missing_version_to_unversioned(
    monkeypatch, env_report, expected
):
    from app.services.ai.adapters import codex_daemon as mod

    _install_resolver_fakes(
        monkeypatch,
        device_id="77",
        rows=[{"id": "77", "env_report": env_report}],
    )
    assert await mod._reported_daemon_version("u1") == expected


@pytest.mark.asyncio
async def test_resolver_returns_none_when_nothing_is_connected(monkeypatch):
    from app.services.ai.adapters import codex_daemon as mod

    _install_resolver_fakes(monkeypatch, device_id=None, rows=[])
    assert await mod._reported_daemon_version("u1") is None


@pytest.mark.asyncio
async def test_resolver_reads_the_row_of_the_device_that_is_actually_online(
    monkeypatch,
):
    """Two paired machines, one connected. Reading the wrong row would gate on
    a version nobody is running — and with the newest row first (list is
    ordered by created_at desc) a naive "take the first" implementation looks
    right until the user connects their older laptop."""
    from app.services.ai.adapters import codex_daemon as mod

    _install_resolver_fakes(
        monkeypatch,
        device_id="55",
        rows=[
            {"id": "99", "env_report": {"daemon_version": "0.3.0"}},
            {"id": "55", "env_report": {"daemon_version": "0.1.0"}},
        ],
    )
    assert await mod._reported_daemon_version("u1") == "0.1.0"


@pytest.mark.asyncio
async def test_resolver_declines_to_judge_when_presence_and_table_disagree(
    monkeypatch,
):
    """Revoked mid-flight / replica lag: presence names a device the table no
    longer lists. Not enough to convict a version on — fall through to the
    offline path instead of inventing "outdated"."""
    from app.services.ai.adapters import codex_daemon as mod

    _install_resolver_fakes(
        monkeypatch,
        device_id="55",
        rows=[{"id": "99", "env_report": {"daemon_version": "0.3.0"}}],
    )
    assert await mod._reported_daemon_version("u1") is None
