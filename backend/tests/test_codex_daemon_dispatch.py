"""C4 — dispatch a canvas job to the user's own daemon, ACROSS containers.

The socket lives in the gateway process; the workflow runs in the worker.
Dispatch therefore rides Redis (presence marker / jobs channel / results
channel) — a transport seam keeps these tests hermetic.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from app.services.codex.daemon_dispatch import (
    DaemonOfflineError,
    DaemonUpdateRequiredError,
    dispatch_to_daemon,
)


class _FakeTransport:
    def __init__(self, online: bool, result: dict | None = None, delay: float = 0.05):
        self.online = online
        self.result = result
        self.delay = delay
        self.sent: list[dict] = []

    async def is_online(self, user_id: str) -> bool:
        return self.online

    async def send_job(self, user_id: str, job: dict) -> None:
        self.sent.append(job)

    async def wait_result(self, job_id: str, timeout_s: float) -> dict:
        await asyncio.sleep(self.delay)
        if self.result is None:
            raise asyncio.TimeoutError()
        return self.result


@pytest.mark.asyncio
async def test_offline_daemon_raises_typed_error_immediately():
    with pytest.raises(DaemonOfflineError):
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={"prompt": "x"},
            transport=_FakeTransport(online=False),
            mint_ticket=lambda **_: "t",
            timeout_s=1,
        )


@pytest.mark.asyncio
async def test_dispatch_sends_job_with_upload_ticket_and_awaits_result():
    t = _FakeTransport(online=True, result={"gen_id": "991"})
    out = await dispatch_to_daemon(
        user_id="u1",
        scope_id=42,
        kind="image",
        payload={"prompt": "a cat"},
        transport=t,
        mint_ticket=lambda **_: "TICKET",
        timeout_s=5,
    )
    assert out == {"gen_id": "991"}
    sent = t.sent[0]
    assert sent["kind"] == "image"
    assert sent["payload"]["upload_ticket"] == "TICKET"
    assert sent["payload"]["prompt"] == "a cat"


@pytest.mark.asyncio
async def test_dispatch_times_out_into_a_typed_failure():
    with pytest.raises(TimeoutError):
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={},
            transport=_FakeTransport(online=True, result=None),
            mint_ticket=lambda **_: "t",
            timeout_s=0.05,
        )


@pytest.mark.asyncio
async def test_daemon_reported_failure_surfaces_as_runtime_error():
    t = _FakeTransport(
        online=True, result={"error": "codex_not_logged_in: run codex login"}
    )
    with pytest.raises(RuntimeError, match="codex_not_logged_in"):
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={},
            transport=t,
            mint_ticket=lambda **_: "t",
            timeout_s=5,
        )


@pytest.mark.asyncio
async def test_persist_step_passes_through_a_daemon_registered_file():
    from app.workflows.canvas_generation import persist_canvas_generation_step

    out = await persist_canvas_generation_step(
        media={
            "media_kind": "image",
            "existing_gen_id": "991",
            "provider": "codex-local",
            "model": "gpt-image-2",
            "local_path": None,
        },
        user_id="u1",
        canvas_id=1,
        node_id="n1",
        prompt="x",
        params={},
    )
    assert out["generated_media_id"] == "991"
    assert out["result_url"] == "/api/v1/generated-media/991/cover"


# ── image/video version gate (P3) ─────────────────────────────────────────
#
# The daemon that forwards ``--quality`` is 0.4.0. Below that the knob is
# discarded one layer down, so the user picks a quality and gets whatever the
# daemon defaults to — a fake switch. The honest answer is a typed refusal
# that says how to update.


async def _v(version):
    async def resolver(_user_id: str):
        return version

    return resolver


@pytest.mark.asyncio
async def test_old_daemon_is_refused_with_a_typed_update_error_before_any_send():
    t = _FakeTransport(online=True, result={"gen_id": "1"})
    with pytest.raises(DaemonUpdateRequiredError) as exc:
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={"engine": "codex", "prompt": "x"},
            transport=t,
            mint_ticket=lambda **_: "t",
            timeout_s=1,
            daemon_version=await _v("0.3.0"),
        )
    assert t.sent == []  # refused BEFORE the job left
    assert "0.3.0" in str(exc.value) and "0.4.0" in str(exc.value)
    # Followable as written: the bare "re-run install.sh" this used to say
    # lands on a path that demands a pairing code and dies without one, and
    # the reader of this message is already paired. --update is the route
    # that keeps their token.
    assert "install.sh | sh -s -- --update" in str(exc.value)


@pytest.mark.asyncio
async def test_refusal_message_is_pure_ascii():
    """The message must survive `public.dbos_error_to_text()` intact.

    That extractor (migration 219) escape-renders the pickle DBOS stores in
    ``dbos.workflow_status.error``, replaces every byte >= 0x80 with a
    delimiter, and keeps only the LONGEST chunk. So a single non-ASCII
    character silently deletes whichever half of the sentence is shorter.
    Verified against the live nous-db: with an em-dash, the reported version
    and the required minimum were both dropped and the user was left with the
    tail alone — the half that does not say what is wrong. Anyone tempted to
    put nicer punctuation back has to delete this test first.
    """
    t = _FakeTransport(online=True, result={"gen_id": "1"})
    with pytest.raises(DaemonUpdateRequiredError) as exc:
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={"engine": "codex", "prompt": "x"},
            transport=t,
            mint_ticket=lambda **_: "t",
            timeout_s=1,
            daemon_version=await _v("0.3.0"),
        )
    assert all(ord(c) < 128 for c in str(exc.value))


@pytest.mark.asyncio
async def test_current_daemon_passes_the_gate():
    t = _FakeTransport(online=True, result={"gen_id": "1"})
    out = await dispatch_to_daemon(
        user_id="u1",
        scope_id=1,
        kind="image",
        payload={"engine": "codex", "prompt": "x"},
        transport=t,
        mint_ticket=lambda **_: "t",
        timeout_s=1,
        daemon_version=await _v("0.4.0"),
    )
    assert out == {"gen_id": "1"} and len(t.sent) == 1


@pytest.mark.asyncio
async def test_unversioned_daemon_is_refused():
    t = _FakeTransport(online=True, result={"gen_id": "1"})
    with pytest.raises(DaemonUpdateRequiredError):
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={"engine": "codex", "prompt": "x"},
            transport=t,
            mint_ticket=lambda **_: "t",
            timeout_s=1,
            daemon_version=await _v("0.0.0"),
        )
    assert t.sent == []


@pytest.mark.asyncio
async def test_unknown_version_does_not_refuse_the_job():
    # None = "no daemon / could not find out", never "too old". This is the
    # branch that has to be pinned on an ONLINE transport: with an offline one
    # the daemon_offline check fires first and the resolver is never consulted,
    # so deleting the whole None-skip would leave such a test green.
    t = _FakeTransport(online=True, result={"gen_id": "1"})
    out = await dispatch_to_daemon(
        user_id="u1",
        scope_id=1,
        kind="image",
        payload={"engine": "codex", "prompt": "x"},
        transport=t,
        mint_ticket=lambda **_: "t",
        timeout_s=1,
        daemon_version=await _v(None),
    )
    assert out == {"gen_id": "1"} and len(t.sent) == 1


@pytest.mark.asyncio
async def test_offline_is_answered_before_the_gate_is_even_consulted():
    # Ordering, as its own property. The resolver reports an OLD version on
    # purpose: if the gate ran first, this would raise "please update" about a
    # daemon that is not even running — the worse of the two wrong answers.
    asked: list[str] = []

    async def resolver(user_id: str):
        asked.append(user_id)
        return "0.3.0"

    t = _FakeTransport(online=False)
    with pytest.raises(DaemonOfflineError):
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={"engine": "codex", "prompt": "x"},
            transport=t,
            mint_ticket=lambda **_: "t",
            timeout_s=1,
            daemon_version=resolver,
        )
    assert asked == []


@pytest.mark.asyncio
async def test_dreamina_jobs_are_not_gated_this_release():
    # Its payload did not change; gating it would refuse working daemons for
    # nothing.
    t = _FakeTransport(online=True, result={"gen_id": "1"})
    out = await dispatch_to_daemon(
        user_id="u1",
        scope_id=1,
        kind="image",
        payload={"engine": "dreamina", "submit_args": ["text2image"]},
        transport=t,
        mint_ticket=lambda **_: "t",
        timeout_s=1,
        daemon_version=await _v("0.3.0"),
    )
    assert out == {"gen_id": "1"}


def test_the_command_the_refusal_names_is_one_the_installer_accepts():
    """The message is only useful if that flag exists on the other side.

    This is the cross-file half of the fix: the refusal used to name a bare
    ``install.sh`` run, which the installer answers by demanding a pairing
    code the reader does not have. Nothing connected the two files, so the
    wording and the script could drift apart without a single test failing.
    Reads the installer rather than restating it.
    """
    from app.services.codex.daemon_dispatch import _UPDATE_COMMAND

    install_sh = (
        pathlib.Path(__file__).resolve().parents[2]
        / "tools"
        / "codex-daemon"
        / "install.sh"
    )
    script = install_sh.read_text()  # missing file must fail, not skip
    assert "--update" in _UPDATE_COMMAND
    # The flag is parsed, not merely mentioned in a comment.
    assert 'if [ "${1-}" = "--update" ]; then' in script
    # And the exact one-liner the user is told to paste is documented there.
    assert _UPDATE_COMMAND in script
