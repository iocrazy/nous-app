"""Producer-wiring tests for the narrow inbox (W3d).

The original W3d inbox had EXACTLY three producers. M2 PR-E (E1) deliberately
widened ``NotificationKind`` with a fourth, ``workflow_stage``, for the
best-effort stage arrival/completion/reopen notifications
(``services/workflow/stage_notifications.py``) — a narrow, spec'd exception to
the "no more producers" rule, not a drift. These static-source checks assert
each of the original three producers is still wired with the correct kind +
typed link, and — as a guardrail against a FIFTH kind sneaking in — that the
``notify()`` call graph's allowed kinds match exactly this set of four.
Mirrors the inspect.getsource style already used by
test_download_workflow_raise_on_error.py (no DBOS runtime needed).
"""

from __future__ import annotations

import inspect

from app.workflows import download as download_mod
from app.workflows import publish_distribution as publish_mod
from app.workflows import scheduled_master as sched_mod


def test_autopilot_producer_wires_issue_link() -> None:
    src = inspect.getsource(sched_mod._fire_agent_routine)
    assert "_inbox_notify(" in src
    assert 'kind="autopilot_output"' in src
    assert 'link_kind="issue"' in src
    # link_id is the human identifier the issues route consumes.
    assert "issue_identifier" in src


def test_publish_producer_step_kind_and_link() -> None:
    step_src = inspect.getsource(publish_mod.emit_publish_notification_step)
    assert 'kind="publish_result"' in step_src
    assert 'link_kind="publish_batch"' in step_src

    wf_src = inspect.getsource(publish_mod.publish_distribution_workflow)
    # Wired at success + all three failure terminals.
    assert wf_src.count("emit_publish_notification_step(") == 4
    assert '"success"' in wf_src and '"error"' in wf_src


def test_generation_producer_step_kind_and_link() -> None:
    step_src = inspect.getsource(download_mod.emit_download_notification_step)
    assert 'kind="generation_result"' in step_src
    # Deep-links to the resource when one exists.
    assert '"resource"' in step_src

    wf_src = inspect.getsource(download_mod.download_workflow)
    # Wired at both success terminals (cache-hit + normal) and both failures.
    assert wf_src.count("emit_download_notification_step(") == 4


def test_no_sixth_producer_kind_leaks_in() -> None:
    """The five allowed kinds are the only ones the notify Literal admits (the
    original three, M2 PR-E's spec'd ``workflow_stage``, and mig 399's spec'd
    ``agent_question`` — an agent paused waiting for the user's answer,
    produced by ``agent_framework/input_gate.py``). A drift here means someone
    widened the inbox beyond the (now five-kind) contract."""
    from app.services.notifications import NotificationKind

    # typing.Literal args carry the exact allowed set.
    allowed = set(getattr(NotificationKind, "__args__", ()))
    assert allowed == {
        "generation_result",
        "publish_result",
        "autopilot_output",
        "workflow_stage",
        "agent_question",
    }


def test_agent_question_producer_wires_issue_link() -> None:
    """input_gate is the only agent_question producer, and it deep-links the
    notification to the asking issue (link_kind='issue')."""
    from app.agent_framework import input_gate

    src = inspect.getsource(input_gate.mark_awaiting_input)
    assert '"agent_question"' in src
    assert 'link_kind="issue"' in src
