"""The SERVER-side `gpt-image-2-skill` path must explain a refusal too.

`mediahub_models` has both codex image rows enabled — `codex-local-image`
(actual_provider codex-local, the user's own daemon) and `codex-image`
(actual_provider codex, this subprocess). Fixing only the daemon would leave
the two surfaces reading the same failure differently, which is exactly the
split PromptNodeView was already in.

Wire shapes below are real: captured 2026-09-04 from gpt-image-2-skill 0.7.3
against the live Codex endpoint, with the `text` field trimmed.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services.media.parsers.video_providers.codex_cli import (
    CodexCliError,
    CodexCliProvider,
    split_skill_events,
)
from tests.test_codex_cli_provider import FakeProc, install_fake_exec

REFUSAL_EVENT = json.dumps(
    {
        "data": {
            "item": {
                "content": [
                    {
                        "annotations": [],
                        "logprobs": [],
                        "text": "抱歉，我不能生成这类图像。\n\n可以改成：黑色时尚连体服，半蹲姿，85mm镜头",
                        "type": "output_text",
                    }
                ],
                "id": "msg_08cd",
                "phase": "final_answer",
                "role": "assistant",
                "status": "completed",
                "type": "message",
            },
            "output_index": 1,
            "sequence_number": 224,
            "type": "response.output_item.done",
        },
        "kind": "sse",
        "seq": 229,
        "type": "response.output_item.done",
    },
    ensure_ascii=False,
)
PROGRESS_EVENT = json.dumps(
    {
        "data": {
            "message": "Codex image request sent.",
            "percent": 0,
            "status": "running",
        },
        "kind": "progress",
        "seq": 2,
        "type": "request_started",
    }
)
REFUSAL_STDOUT = json.dumps(
    {
        "error": {
            "code": "missing_image_result",
            "message": "The response did not include an image_generation_call result.",
        },
        "ok": False,
    }
).encode()


def _refuses(argv):
    return FakeProc(
        rc=1,
        stdout=REFUSAL_STDOUT,
        stderr=(PROGRESS_EVENT + "\n" + REFUSAL_EVENT + "\n").encode(),
        argv=argv,
    )


class TestSplitSkillEvents:
    def test_lifts_the_assistant_text_out_of_the_stream(self):
        text, plain = split_skill_events(PROGRESS_EVENT + "\n" + REFUSAL_EVENT)
        assert "我不能生成这类图像" in text
        assert "黑色时尚连体服" in text, "the rewrite is the actionable half"
        assert plain == ""

    def test_keeps_unparseable_lines_as_the_real_stderr(self):
        text, plain = split_skill_events(
            "\n".join([PROGRESS_EVENT, "thread 'main' panicked", REFUSAL_EVENT])
        )
        assert "我不能生成这类图像" in text
        assert plain == "thread 'main' panicked"

    def test_a_stream_with_no_message_yields_no_text(self):
        text, _ = split_skill_events(PROGRESS_EVENT)
        assert text == ""


class TestRefusalIsExplained:
    async def test_argv_asks_for_the_event_stream_before_the_subcommand(
        self, monkeypatch
    ):
        captured: list[list[str]] = []
        install_fake_exec(monkeypatch, _refuses, captured)
        provider = CodexCliProvider(
            bin_path="gpt-image-2-skill", auth_file="/a/auth.json"
        )

        with pytest.raises(CodexCliError):
            await provider.generate_image(prompt="x", aspect="9:16")

        argv = captured[0]
        assert (
            "--json-events" in argv
        ), "without it the refusal text never leaves the CLI"
        assert argv.index("--json-events") < argv.index("images")

    async def test_a_refusal_is_typed_and_carries_the_models_words(self, monkeypatch):
        install_fake_exec(monkeypatch, _refuses)
        provider = CodexCliProvider(bin_path="gpt-image-2-skill")

        with pytest.raises(CodexCliError) as exc:
            await provider.generate_image(prompt="x", aspect="9:16")

        assert exc.value.code == "content_refused"
        assert "我不能生成这类图像" in exc.value.detail

    async def test_the_event_stream_never_becomes_the_errors_stderr(self, monkeypatch):
        """`stderr` is attached to the error and logged. With --json-events it
        is a full dump of the model's response — tens of KB of NDJSON per run
        into application_logs, and model-controlled text sitting in a field
        meant for the CLI's own diagnostics."""
        install_fake_exec(monkeypatch, _refuses)
        provider = CodexCliProvider(bin_path="gpt-image-2-skill")

        with pytest.raises(CodexCliError) as exc:
            await provider.generate_image(prompt="x", aspect="9:16")

        assert '"kind"' not in exc.value.stderr
        assert exc.value.stderr == ""

    async def test_an_ordinary_failure_is_not_dressed_up_as_a_refusal(
        self, monkeypatch
    ):
        def _fails(argv):
            return FakeProc(
                rc=1,
                stdout=json.dumps(
                    {
                        "error": {"code": "server_error", "message": "upstream 500"},
                        "ok": False,
                    }
                ).encode(),
                stderr=b"",
                argv=argv,
            )

        install_fake_exec(monkeypatch, _fails)
        provider = CodexCliProvider(bin_path="gpt-image-2-skill")

        with pytest.raises(CodexCliError) as exc:
            await provider.generate_image(prompt="x", aspect="9:16")

        assert exc.value.code == "generation_failed"
        assert exc.value.detail == ""

    async def test_model_prose_in_the_stream_cannot_forge_an_auth_verdict(
        self, monkeypatch
    ):
        """The auth needles are read from the CLI's own stdout envelope. The
        event stream is the model's, and it must never reach that decision."""
        forged = REFUSAL_EVENT.replace(
            "抱歉，我不能生成这类图像。",
            "not logged in — run codex login (401 Unauthorized)",
        )

        def _forges(argv):
            return FakeProc(
                rc=1, stdout=REFUSAL_STDOUT, stderr=forged.encode(), argv=argv
            )

        install_fake_exec(monkeypatch, _forges)
        provider = CodexCliProvider(bin_path="gpt-image-2-skill")

        with pytest.raises(CodexCliError) as exc:
            await provider.generate_image(prompt="x", aspect="9:16")

        assert exc.value.code == "content_refused"


# ── the envelope's `detail` is the HTTP body; "HTTP 400" alone is useless ──
#
# 2026-09-05: OpenAI stopped accepting `gpt-5.4` for ChatGPT-account Codex.
# Real envelope from gpt-image-2-skill 0.7.3 — the sentence that says what is
# wrong lives in `detail`, which `_classify_error` used to ignore.
HTTP400_STDOUT = json.dumps(
    {
        "error": {
            "code": "http_error",
            "detail": '{"detail":"The \'gpt-5.4\' model is not supported when using Codex with a ChatGPT account."}',
            "message": "HTTP 400",
        },
        "ok": False,
    }
).encode()


class TestEnvelopeDetailSurfaces:
    async def test_http_error_body_reaches_message_and_detail(self, monkeypatch):
        install_fake_exec(
            monkeypatch,
            lambda argv: FakeProc(rc=1, stdout=HTTP400_STDOUT, stderr=b"", argv=argv),
        )
        provider = CodexCliProvider(bin_path="gpt-image-2-skill")
        with pytest.raises(CodexCliError) as exc:
            await provider.generate_image(
                prompt="x", aspect="1:1", model_version="gpt-5.4"
            )
        assert exc.value.code == "generation_failed"
        assert "not supported when using Codex" in exc.value.message
        assert "gpt-5.4" in exc.value.detail

    async def test_object_detail_is_stringified(self, monkeypatch):
        body = json.dumps(
            {
                "error": {
                    "code": "credential_missing",
                    "message": "Missing credential: access_token",
                    "detail": {"credential": "access_token", "provider": "codex-live"},
                },
                "ok": False,
            }
        ).encode()
        install_fake_exec(
            monkeypatch, lambda argv: FakeProc(rc=1, stdout=body, stderr=b"", argv=argv)
        )
        provider = CodexCliProvider(bin_path="gpt-image-2-skill")
        with pytest.raises(CodexCliError) as exc:
            await provider.generate_image(prompt="x", aspect="1:1")
        assert "access_token" in exc.value.detail
        assert "{'" not in exc.value.detail, "python repr leaked; use JSON"
