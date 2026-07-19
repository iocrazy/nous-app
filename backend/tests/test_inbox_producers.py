"""Producer-wiring tests for the narrow inbox (W3d).

The inbox has EXACTLY three producers, and no more. These static-source checks
assert each producer is wired with the correct kind + typed link, and — as a
guardrail on the narrowness contract — that the ``notify()`` call graph doesn't
sprout a fourth producer kind. Mirrors the inspect.getsource style already used
by test_download_workflow_raise_on_error.py (no DBOS runtime needed).
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


def test_no_fourth_producer_kind_leaks_in() -> None:
    """The three allowed kinds are the only ones the notify Literal admits.
    A drift here means someone widened the inbox beyond the narrow contract."""
    from app.services.notifications import NotificationKind

    # typing.Literal args carry the exact allowed set.
    allowed = set(getattr(NotificationKind, "__args__", ()))
    assert allowed == {
        "generation_result",
        "publish_result",
        "autopilot_output",
    }
