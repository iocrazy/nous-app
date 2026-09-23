"""``agent_video_workflow``: the half of GenerateVideo that outlives the turn.

One generation step (local daemon or server, decided by the row the catalog
pick lands on), then delivery from the workflow BODY on success AND on
failure: an inbox item for the model, a notification for the user, a
transcript event on the originating run, and — for an issue — the wake-up
decision. A failure is delivered first and then RAISED (route C §4: a
workflow returning a failed dict would read as SUCCESS in task_tracking).
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.generation.local_dispatch import DREAMINA_DISPATCH_TIMEOUT_S
from app.services.generation.ref_urls import absolute_media_url
from app.services.media.parsers.video_providers.db_registry import (
    LocalVideoRoute,
    ServerVideoRoute,
)

_USER = "11111111-1111-1111-1111-111111111111"
_ROUTE_TARGET = (
    "app.services.media.parsers.video_providers.db_registry.resolve_video_route"
)
_DISPATCH_TARGET = "app.services.codex.daemon_dispatch.dispatch_to_daemon"
_LOCAL = LocalVideoRoute(
    engine="dreamina", engine_model="seedance2.0", row_name="jimeng-local-video"
)
_COVER = "/api/v1/generated-media/555/cover"


def _step_kwargs(**over):
    base: dict[str, Any] = dict(
        prompt="pan left",
        source_image_url=_COVER,
        provider="jimeng-local-video",
        model="",
        user_id=_USER,
        team_id=7,
        agent_id="ag-1",
        run_id=4242,
        turn=1,
        step=3,
        conversation_id=9001,
    )
    base.update(over)
    return base


# ── the generation step ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_route_dispatches_to_the_daemon_with_agent_attribution():
    from app.workflows import agent_video as m

    dispatch = AsyncMock(return_value={"gen_id": "777"})
    with (
        patch(_ROUTE_TARGET, new=AsyncMock(return_value=_LOCAL)),
        patch(_DISPATCH_TARGET, new=dispatch),
    ):
        out = await m.generate_agent_video_step(**_step_kwargs())

    assert out == {
        "gen_id": "777",
        "provider": "dreamina-local",
        "model": "seedance2.0",
    }
    kw = dispatch.await_args.kwargs
    assert kw["kind"] == "video"
    # The daemon's own budget, not the tool's 600s.
    assert kw["timeout_s"] == DREAMINA_DISPATCH_TIMEOUT_S
    assert kw["scope_id"] == 7
    assert kw["user_id"] == _USER
    payload = kw["payload"]
    assert payload["engine"] == "dreamina"
    assert payload["ref_urls"] == [absolute_media_url(_COVER)]
    assert payload["submit_args"][0] == "image2video"
    a = kw["attribution"]
    assert a["kind"] == "agent_run"
    assert a["derivation_kind"] == "video_gen"
    assert (a["run_id"], a["turn"], a["step"]) == (4242, 1, 3)
    assert a["agent_id"] == "ag-1"
    assert a["conversation_id"] == 9001
    assert a["provider"] == "dreamina-local"
    assert a["model"] == "seedance2.0"
    assert a["prompt"] == "pan left"
    # The user's own machine and account: never a platform charge.
    assert a["byok"] is True


@pytest.mark.asyncio
async def test_local_route_personal_scope_when_there_is_no_team(monkeypatch):
    from app.workflows import agent_video as m

    async def _personal(uid):
        assert uid == _USER
        return "900"

    monkeypatch.setattr(m, "_resolve_personal_team_id", _personal)
    dispatch = AsyncMock(return_value={"gen_id": "1"})
    with (
        patch(_ROUTE_TARGET, new=AsyncMock(return_value=_LOCAL)),
        patch(_DISPATCH_TARGET, new=dispatch),
    ):
        await m.generate_agent_video_step(**_step_kwargs(team_id=None))
    assert dispatch.await_args.kwargs["scope_id"] == 900


@pytest.mark.asyncio
async def test_local_offline_daemon_is_returned_with_its_code_not_raised():
    """A raise would be a retry of an offline daemon (or of a PAID timed-out
    job); the marker comes back, carrying the typed code for delivery."""
    from app.services.codex.daemon_dispatch import DaemonOfflineError
    from app.services.generation import local_dispatch
    from app.workflows import agent_video as m

    patched: dict = {}

    async def fake_patch(task_id, patch_dict):
        patched.update(patch_dict)

    dispatch = AsyncMock(side_effect=DaemonOfflineError("[daemon_offline] off"))
    with (
        patch(_ROUTE_TARGET, new=AsyncMock(return_value=_LOCAL)),
        patch(_DISPATCH_TARGET, new=dispatch),
        patch.object(local_dispatch, "patch_task_metadata", new=fake_patch),
        patch.object(local_dispatch.DBOS, "workflow_id", "wf-v", create=True),
    ):
        out = await m.generate_agent_video_step(**_step_kwargs())

    assert dispatch.await_count == 1
    assert "[daemon_offline]" in out["failed"]
    assert out["code"] == "daemon_offline"
    assert patched["failure"]["code"] == "daemon_offline"


@pytest.mark.asyncio
async def test_server_route_generates_inside_the_step_and_registers(
    monkeypatch, tmp_path
):
    from app.workflows import agent_video as m

    d = tmp_path / "jimeng_abc"
    d.mkdir()
    clip = d / "out.mp4"
    clip.write_bytes(b"x")
    server = ServerVideoRoute(
        provider=object(), actual_model="seedance", row_name="jimeng-cli-seedance"
    )
    seen: dict = {}

    async def _gen(self, **kwargs):
        seen["gen"] = kwargs
        return {
            "video_url": "",
            "video_path": str(clip),
            "provider": "jimeng-cli",
            "model": "seedance",
        }

    async def _register(**kwargs):
        seen["register"] = kwargs
        return {"id": 888}

    monkeypatch.setattr(
        "app.services.ai.media.image_generation_service.ImageGenerationService"
        ".generate_video",
        _gen,
    )
    monkeypatch.setattr(m, "register_generated_media", _register)
    with patch(_ROUTE_TARGET, new=AsyncMock(return_value=server)):
        out = await m.generate_agent_video_step(
            **_step_kwargs(provider="jimeng-cli-seedance", model="m1")
        )

    assert out == {"gen_id": "888", "provider": "jimeng-cli", "model": "seedance"}
    assert seen["gen"]["source_image_url"] == _COVER
    assert seen["gen"]["provider_name"] == "jimeng-cli-seedance"
    assert seen["gen"]["model"] == "m1"
    reg = seen["register"]
    assert reg["source_path"] == str(clip)
    assert reg.get("source_url") is None
    assert reg["scope_id"] == 7
    assert reg["mime"] == "video/mp4"
    origin = reg["origin"]
    assert origin.kind == "agent_run"
    assert origin.derivation_kind == "video_gen"
    assert (origin.run_id, origin.turn, origin.step) == (4242, 1, 3)
    assert origin.agent_id == "ag-1"
    assert origin.conversation_id == 9001
    assert (origin.provider, origin.model) == ("jimeng-cli", "seedance")
    # The CLI scratch dir is reaped once the clip is filed.
    assert not d.exists()


@pytest.mark.asyncio
async def test_server_route_with_no_product_raises(monkeypatch):
    from app.workflows import agent_video as m

    server = ServerVideoRoute(provider=object(), actual_model="s", row_name="r")

    async def _gen(self, **kwargs):
        return {"video_url": "", "video_path": None}

    monkeypatch.setattr(
        "app.services.ai.media.image_generation_service.ImageGenerationService"
        ".generate_video",
        _gen,
    )
    with (
        patch(_ROUTE_TARGET, new=AsyncMock(return_value=server)),
        pytest.raises(RuntimeError, match="no video"),
    ):
        await m.generate_agent_video_step(**_step_kwargs())


def test_generation_step_is_never_retried():
    """A retry re-submits a paid, long job. Deterministic daemon failures are
    returned, not raised; anything else fails the task once and is delivered
    to the agent, which decides whether to try again."""
    from app.workflows import agent_video as m

    src = inspect.getsource(m)
    assert "@DBOS.step(retries_allowed=False)\nasync def generate_agent_video_step" in (
        src
    )


# ── the workflow body ───────────────────────────────────────────────────


class _Spy:
    def __init__(self, result=None, exc: Exception | None = None):
        self.result = result
        self.exc = exc
        self.calls: list[tuple[tuple, dict]] = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.result


def _wf_kwargs(**over):
    base = dict(
        prompt="pan left",
        source_image_url=_COVER,
        provider="jimeng-local-video",
        model="",
        user_id=_USER,
        team_id=7,
        agent_id="ag-1",
        run_id=4242,
        turn=1,
        step=3,
        conversation_id=9001,
        reply_to_kind="conversation",
        reply_to_id=9001,
    )
    base.update(over)
    return base


@pytest.fixture
def body(monkeypatch):
    from app.services.issues import inbox_or_dispatch
    from app.services.issues.inbox_or_dispatch import DeliverResult
    from app.workflows import agent_video as m

    spies = {
        "gen": _Spy({"gen_id": "777", "provider": "dreamina-local", "model": "x"}),
        "enqueue": _Spy(123),
        "notify": _Spy(None),
        "event": _Spy(None),
        "wake": _Spy(DeliverResult("dispatched", workflow_id="w")),
    }
    monkeypatch.setattr(m, "generate_agent_video_step", spies["gen"])
    monkeypatch.setattr(m, "enqueue_media_result_step", spies["enqueue"])
    monkeypatch.setattr(m, "notify_media_result_step", spies["notify"])
    monkeypatch.setattr(m, "record_media_job_event_step", spies["event"])
    monkeypatch.setattr(inbox_or_dispatch, "deliver_or_dispatch", spies["wake"])
    monkeypatch.setattr(m.DBOS, "workflow_id", "wf-123", raising=False)
    return m, spies


@pytest.mark.asyncio
async def test_success_is_delivered_to_the_inbox_the_user_and_the_run(body):
    m, s = body
    wf = inspect.unwrap(m.agent_video_workflow)

    out = await wf(**_wf_kwargs())

    assert out["status"] == "success"
    assert out["generated_media_id"] == "777"
    assert out["url"] == "/api/v1/generated-media/777/stream"

    (args, kw) = s["enqueue"].calls[0]
    assert kw["target_kind"] == "conversation"
    assert kw["target_id"] == 9001
    assert kw["user_id"] == _USER
    assert kw["dedupe_key"] == "agentvideo-wf-123"
    content = kw["content"]
    assert content["status"] == "completed"
    assert content["generated_media_id"] == "777"
    assert content["url"] == "/api/v1/generated-media/777/stream"
    assert content["error_code"] is None
    assert content["task_id"] == "wf-123"
    assert "/api/v1/generated-media/777/stream" in content["text"]

    (_, nkw) = s["notify"].calls[0]
    assert nkw["status"] == "completed"

    (_, ekw) = s["event"].calls[0]
    assert ekw["run_id"] == 4242
    assert ekw["payload"]["status"] == "completed"
    assert ekw["payload"]["generated_media_id"] == "777"
    assert ekw["payload"]["task_id"] == "wf-123"
    assert (ekw["turn"], ekw["step"]) == (1, 3)

    # A conversation has no turn to start: nothing wakes it.
    assert s["wake"].calls == []


@pytest.mark.asyncio
async def test_issue_target_asks_for_the_wake_up_with_a_replay_stable_key(body):
    m, s = body
    wf = inspect.unwrap(m.agent_video_workflow)

    await wf(**_wf_kwargs(reply_to_kind="issue", reply_to_id=77))

    (args, kw) = s["wake"].calls[0]
    assert args == (77,)
    assert kw["already_enqueued"] is True
    assert kw["kind"] == "media_result"
    assert kw["user_id"] == _USER
    assert kw["dedupe_key"] == "agentvideo-wake-wf-123"
    assert s["enqueue"].calls[0][1]["target_kind"] == "issue"


@pytest.mark.asyncio
async def test_typed_failure_is_delivered_then_raised(body):
    m, s = body
    s["gen"].result = {
        "failed": "[daemon_offline] not connected",
        "code": "daemon_offline",
    }
    wf = inspect.unwrap(m.agent_video_workflow)

    with pytest.raises(RuntimeError, match="daemon_offline"):
        await wf(**_wf_kwargs(reply_to_kind="issue", reply_to_id=77))

    content = s["enqueue"].calls[0][1]["content"]
    assert content["status"] == "failed"
    assert content["error_code"] == "daemon_offline"
    assert content["generated_media_id"] is None
    assert content["url"] is None
    assert "daemon_offline" in content["text"]
    assert s["notify"].calls[0][1]["status"] == "failed"
    assert s["event"].calls[0][1]["payload"]["error_code"] == "daemon_offline"
    # Failure still reaches an idle issue.
    assert len(s["wake"].calls) == 1


@pytest.mark.asyncio
async def test_a_raising_step_is_delivered_as_generation_failed_then_raised(body):
    m, s = body
    s["gen"].exc = RuntimeError("jimeng cli exited 1")
    wf = inspect.unwrap(m.agent_video_workflow)

    with pytest.raises(RuntimeError, match="jimeng cli exited 1"):
        await wf(**_wf_kwargs())

    content = s["enqueue"].calls[0][1]["content"]
    assert content["status"] == "failed"
    assert content["error_code"] == "generation_failed"


@pytest.mark.asyncio
async def test_delivery_trouble_never_masks_the_outcome(body):
    """Each delivery arm is independent; a wake that raises must not turn a
    finished video into a failed task."""
    m, s = body
    s["wake"].exc = RuntimeError("db blip")
    wf = inspect.unwrap(m.agent_video_workflow)

    out = await wf(**_wf_kwargs(reply_to_kind="issue", reply_to_id=77))
    assert out["status"] == "success"


# ── the delivery steps ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_step_files_a_media_result_with_the_dedupe_key(monkeypatch):
    """Replay safety: the SAME key on every attempt, so the repository hands
    back the existing row instead of a second copy (mig 462 unique index)."""
    from app.workflows import agent_video as m

    calls: list[dict] = []

    class _Repo:
        async def enqueue(self, **kw):
            calls.append(kw)
            return {"id": 5}

    monkeypatch.setattr(
        "app.repositories.agent_run_inbox_repository.get_agent_run_inbox_repository",
        lambda: _Repo(),
    )
    for _ in range(2):
        out = await m.enqueue_media_result_step(
            target_kind="issue",
            target_id=77,
            user_id=_USER,
            content={"status": "completed"},
            dedupe_key="agentvideo-wf-1",
        )
        assert out == 5
    assert [c["dedupe_key"] for c in calls] == ["agentvideo-wf-1"] * 2
    assert {c["kind"] for c in calls} == {"media_result"}


@pytest.mark.asyncio
async def test_enqueue_step_never_raises(monkeypatch):
    from app.workflows import agent_video as m

    class _Repo:
        async def enqueue(self, **kw):
            raise RuntimeError("down")

    monkeypatch.setattr(
        "app.repositories.agent_run_inbox_repository.get_agent_run_inbox_repository",
        lambda: _Repo(),
    )
    assert (
        await m.enqueue_media_result_step(
            target_kind="issue",
            target_id=1,
            user_id=_USER,
            content={},
            dedupe_key="k",
        )
        is None
    )


@pytest.mark.asyncio
async def test_event_step_appends_media_job_done_on_the_originating_run(monkeypatch):
    from app.services.ai.runner import run_recorder
    from app.workflows import agent_video as m

    appended: list = []

    class _Writer:
        async def append(self, event_type, payload, *, turn=None, step=None):
            appended.append((event_type, payload, turn, step))
            return 1

    async def _for_run(run_id):
        assert run_id == 4242
        return _Writer()

    monkeypatch.setattr(run_recorder.RunEventWriter, "for_run", _for_run)
    await m.record_media_job_event_step(
        run_id=4242, payload={"status": "completed"}, turn=1, step=3
    )
    assert appended == [("media_job_done", {"status": "completed"}, 1, 3)]


@pytest.mark.asyncio
async def test_notify_step_links_an_issue_and_names_the_outcome(monkeypatch):
    from app.services import notifications
    from app.workflows import agent_video as m

    sent: list[dict] = []

    async def _notify(**kw):
        sent.append(kw)
        return 1

    monkeypatch.setattr(notifications, "notify", _notify)
    await m.notify_media_result_step(
        user_id=_USER,
        status="failed",
        target_kind="issue",
        target_id=77,
        error_code="daemon_offline",
    )
    await m.notify_media_result_step(
        user_id=_USER,
        status="completed",
        target_kind="conversation",
        target_id=9001,
        error_code=None,
    )
    fail, ok = sent
    assert fail["kind"] == "generation_result"
    assert fail["severity"] == "error"
    assert (fail["link_kind"], fail["link_id"]) == ("issue", "77")
    assert "daemon_offline" in fail["body"]
    assert ok["severity"] == "success"
    assert ok["link_kind"] is None


def test_workflow_is_exported_from_the_dispatch_bundle():
    from app.workflows import _dispatch_bundle
    from app.workflows.agent_video import agent_video_workflow

    assert _dispatch_bundle.agent_video_workflow is agent_video_workflow


# ── billing ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_completion_landing_after_the_tree_settled_is_never_charged(
    monkeypatch,
):
    """The video lands long after the run tree was settled ("一树一扣"): the
    ``deliverable`` it registers folds a price into the ENDED run's
    ``media_cents``, but the tree's ``charged_at`` stamp is already there, so
    no settle ever charges it again ("只向前不追扣").

    Today that costs nothing: local rows run on the user's own account
    (ticket ``byok``) and the server row ``jimeng-cli-seedance`` has no
    ``ai_model_prices`` row. A future PRICED server route must therefore
    charge at SUBMIT — an async completion is structurally free.
    """
    from app.services.ai.billing import tree_charge
    from app.services.ai.billing.token_billing import ReconcileResult
    from tests.runner.test_root_once_charge import _db, _Row

    charged = AsyncMock(
        return_value=ReconcileResult(
            charged=True, charged_points=1.0, byo_key=False, usage_logged=False
        )
    )
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", charged)
    _db(
        monkeypatch,
        my_root=None,
        tree_rows=[
            _Row(
                800000000000001,
                cost={"own_cents": 1.0, "media_cents": 250.0},
                billing={"charged_at": "2026-09-22T00:00:00+00:00"},
            )
        ],
        ever_charged=True,
        cas_rowcount=0,
    )
    out = await tree_charge.settle_tree_if_closed(run_id="800000000000001")
    assert (out.settled, out.reason) == (False, "already")
    charged.assert_not_awaited()
