"""A content refusal is an ANSWER, not a crash (2026-09-04).

`gpt-image-2-skill` reports a policy refusal as `missing_image_result` — a
statement about the pipeline's shape, not about the request — while the
model's own explanation (which names the offending words AND offers a
rewrite that works) is dropped. The daemon now captures that text; these
tests pin the two seams it has to cross to reach the user.

The hard constraint shaping all of this: the refusal text is the MODEL's
prose, so it is routinely non-ASCII. It therefore cannot ride
`task_tracking.error_msg` — `public.dbos_error_to_text()` (migration 219)
escape-renders the pickle DBOS stores, turns every byte >= 0x80 into a
delimiter and keeps only the longest surviving chunk, which would shred any
Chinese sentence into a fragment. The text rides `metadata` (business
decoration, route C §3); `error_msg` gets a single ASCII line.
"""

from __future__ import annotations

import asyncio

import pytest

from app.api.codex_daemon_ws_router import assemble_job_failure
from app.services.codex.daemon_dispatch import (
    DaemonJobFailedError,
    dispatch_to_daemon,
)
from app.services.codex.errors import from_daemon_error
from app.services.generation.failure import describe_generation_failure

REFUSAL_TEXT = (
    "抱歉，我不能帮助生成带有明显性化服饰与姿势的写实人物图像。\n\n"
    "你也可以直接用这条更安全的提示词：成年年轻女性，黑色时尚连体服与长靴，"
    "半蹲姿，写实摄影风格，85mm镜头"
)


class _FakeTransport:
    def __init__(self, result: dict):
        self.result = result
        self.sent: list[dict] = []

    async def is_online(self, user_id: str) -> bool:
        return True

    async def send_job(self, user_id: str, job: dict) -> None:
        self.sent.append(job)

    async def wait_result(self, job_id: str, timeout_s: float) -> dict:
        await asyncio.sleep(0)
        return self.result


# ── the WS seam ────────────────────────────────────────────────────────────


def test_job_failed_frame_carries_the_models_words_through():
    out = assemble_job_failure(
        {
            "type": "job_failed",
            "job_id": "j1",
            "code": "content_refused",
            "message": "gpt-image-2-skill: missing_image_result: ...",
            "detail": REFUSAL_TEXT,
        }
    )
    assert out["error"].startswith("content_refused: ")
    assert out["error_detail"] == REFUSAL_TEXT


def test_job_failed_without_a_detail_says_nothing_rather_than_empty():
    """A 0.4.0 daemon sends no `detail`. Absent must read as absent — an empty
    string here would make the UI render a blank explanation panel."""
    out = assemble_job_failure({"code": "job_failed", "message": "boom"})
    assert out["error"] == "job_failed: boom"
    assert "error_detail" not in out


def test_a_detail_that_is_not_text_is_dropped_rather_than_trusted():
    """The frame comes off a socket. Shape is not a promise."""
    for bogus in (123, {"a": 1}, ["x"], None):
        out = assemble_job_failure({"code": "x", "message": "m", "detail": bogus})
        assert "error_detail" not in out


# ── the dispatch seam ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_raises_a_typed_error_carrying_code_and_detail():
    t = _FakeTransport(
        {"error": "content_refused: declined", "error_detail": REFUSAL_TEXT}
    )
    with pytest.raises(DaemonJobFailedError) as exc:
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="image",
            payload={"engine": "codex", "prompt": "x"},
            transport=t,
            mint_ticket=lambda **_: "t",
            timeout_s=1,
            daemon_version=_version("0.5.0"),
        )
    assert exc.value.code == "content_refused"
    assert exc.value.detail == REFUSAL_TEXT


@pytest.mark.asyncio
async def test_the_raised_message_shape_still_feeds_from_daemon_error():
    """`errors.from_daemon_error` splits `str(exc)` on the first colon. The new
    subclass must not change that string, or every codex-local LLM failure
    silently collapses to the generic `codex_failed`."""
    t = _FakeTransport({"error": "codex_not_logged_in: run codex login"})
    with pytest.raises(DaemonJobFailedError) as exc:
        await dispatch_to_daemon(
            user_id="u1",
            scope_id=1,
            kind="text",
            payload={"prompt": "x"},
            transport=t,
            mint_ticket=lambda **_: "t",
            timeout_s=1,
        )
    assert str(exc.value) == "codex_not_logged_in: run codex login"
    assert from_daemon_error(str(exc.value)).code == "codex_not_logged_in"


# ── the split: ASCII line for error_msg, model prose for metadata ──────────


def test_refusal_message_is_one_line_of_pure_ascii():
    """Two properties, asserted separately so either can fail on its own:
    non-ASCII gets the message shredded by dbos_error_to_text, and a newline
    gets everything but the longest line thrown away."""
    message, _ = describe_generation_failure(
        DaemonJobFailedError(
            "content_refused: declined", code="content_refused", detail=REFUSAL_TEXT
        )
    )
    assert all(ord(c) < 128 for c in message)
    assert "\n" not in message


def test_refusal_message_carries_a_marker_the_ui_can_match_after_shredding():
    message, _ = describe_generation_failure(
        DaemonJobFailedError(
            "content_refused: declined", code="content_refused", detail=REFUSAL_TEXT
        )
    )
    assert "content_refused" in message


def test_refusal_metadata_carries_the_models_own_words_verbatim():
    """jsonb, not error_msg — this is the half that is allowed to be Chinese."""
    _, patch = describe_generation_failure(
        DaemonJobFailedError(
            "content_refused: declined", code="content_refused", detail=REFUSAL_TEXT
        )
    )
    assert patch["failure"]["code"] == "content_refused"
    assert patch["failure"]["detail"] == REFUSAL_TEXT


def test_a_plain_daemon_failure_is_never_dressed_up_as_a_refusal():
    """`describe_daemon_failure` must not invent a refusal for an ordinary
    crash — "the model declined you" is a wrong and unfixable thing to tell
    someone whose daemon simply died."""
    message, patch = describe_generation_failure(
        DaemonJobFailedError(
            "job_failed: gpt-image-2-skill exited 1", code="job_failed", detail=""
        )
    )
    assert "content_refused" not in message
    assert patch["failure"]["code"] == "job_failed"
    assert patch["failure"]["detail"] == ""


def _version(v: str):
    async def _resolver(_user_id: str):
        return v

    return _resolver


# ── the workflow seam: which column each half lands in ─────────────────────


@pytest.mark.asyncio
async def test_the_workflow_persists_the_refusal_and_raises_a_clean_line():
    """The daemon branch of ``generate_canvas_media_step`` has to do BOTH:
    write the model's words where jsonb preserves them, and raise something
    the DBOS mirror can render. Doing only the second is the bug being fixed
    (the user got `RuntimeError: "message": "The response did not include an
    image_generation_call result."` and nothing else); doing only the first
    would mark the task completed."""
    from unittest.mock import AsyncMock, patch

    import app.workflows.canvas_generation as m
    from app.workflows.canvas_generation import generate_canvas_media_step

    patched: dict = {}

    async def fake_dispatch(**_kw):
        raise DaemonJobFailedError(
            "content_refused: gpt-image-2-skill: missing_image_result: ...",
            code="content_refused",
            detail=REFUSAL_TEXT,
        )

    async def fake_patch_metadata(task_id, patch_dict):
        patched.update(patch_dict)

    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=("codex", "gpt-image-2")),
        ),
        patch(
            "app.services.codex.daemon_dispatch.dispatch_to_daemon", new=fake_dispatch
        ),
        patch(
            "app.workflows.canvas_generation._resolve_personal_team_id",
            new=AsyncMock(return_value=7),
        ),
        patch(
            "app.workflows.canvas_generation._patch_task_metadata",
            new=fake_patch_metadata,
        ),
        # The step reads the task row's id off the ambient workflow, the same
        # way record_canvas_generation_result_step does.
        patch.object(m.DBOS, "workflow_id", "wf-refusal-1", create=True),
        pytest.raises(RuntimeError) as exc,
    ):
        await generate_canvas_media_step(
            kind="image",
            prompt="a cat",
            model="codex-local-image",
            params={"ratio": "9:16"},
            source_url=None,
            user_id="u1",
        )

    assert patched["failure"]["code"] == "content_refused"
    assert patched["failure"]["detail"] == REFUSAL_TEXT
    assert all(ord(c) < 128 for c in str(exc.value))
    assert "content_refused" in str(exc.value)


def test_content_refused_is_a_non_retryable_4xx_on_the_llm_path():
    """errors._STATUS_BY_CODE drives the retry middleware: a 4xx is
    non_retryable, which is what keeps a codex-local failure from falling
    through to a PAID model the user never agreed to pay for. An unknown code
    collapses to codex_failed, so this has to be registered explicitly."""
    from app.services.codex.errors import CodexLocalError

    err = CodexLocalError("content_refused", "declined")
    assert err.code == "content_refused"
    assert 400 <= err.status_code < 500


@pytest.mark.asyncio
async def test_the_server_side_codex_branch_records_the_refusal_too():
    """`mediahub_models` has BOTH codex image rows enabled: codex-local-image
    (the daemon) and codex-image (the in-container subprocess). The two
    surfaces must read the same failure the same way — fixing one and not the
    other is how PromptNodeView drifted from Task Center in the first place."""
    from unittest.mock import AsyncMock, patch

    from app.services.media.parsers.video_providers.codex_cli import CodexCliError
    from app.workflows.canvas_generation import generate_canvas_media_step

    patched: dict = {}

    class _Provider:
        async def generate(self, *_a, **_kw):
            raise CodexCliError(
                "content_refused",
                "the image model declined this prompt and answered with an "
                "explanation instead of an image",
                detail=REFUSAL_TEXT,
            )

    async def fake_patch_metadata(task_id, patch_dict):
        patched.update(patch_dict)

    import app.workflows.canvas_generation as m

    with (
        patch(
            "app.workflows.canvas_generation._local_engine",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.media.parsers.video_providers.db_registry.resolve_image_provider",
            new=AsyncMock(return_value=(_Provider(), "gpt-5.4")),
        ),
        patch(
            "app.workflows.canvas_generation._patch_task_metadata",
            new=fake_patch_metadata,
        ),
        patch.object(m.DBOS, "workflow_id", "wf-refusal-2", create=True),
        pytest.raises(RuntimeError) as exc,
    ):
        await generate_canvas_media_step(
            kind="image",
            prompt="a cat",
            model="codex-image",
            params={"ratio": "9:16"},
            source_url=None,
            user_id="u1",
        )

    assert patched["failure"]["code"] == "content_refused"
    assert patched["failure"]["detail"] == REFUSAL_TEXT
    assert all(ord(c) < 128 for c in str(exc.value))
    assert "content_refused" in str(exc.value)
