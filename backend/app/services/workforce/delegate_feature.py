"""Feature gate for the Workforce Delegate tool (harness audit #4).

The Delegate → agent_inbox → task_tracking → DBOS ``agent_workforce_workflow``
→ ``run_one_task`` chain is WIRED as of phase 2b-2 T3. The four wires this
module used to wait on are all in place:

  1. the scheduled inbox tick shapes a dispatch queue
     (``workforce_dispatch.inbox_dispatch_tick_step``) and the workflow BODY
     enqueues it through ``DbosAgentWorkforcePool`` — never the step, which
     route C forbids from starting a workflow;
  2. ``AgentWorkforceRepository.mark_dispatched`` stamps what went out so the
     next tick does not re-enqueue it;
  3. ``inflight_count`` is derived from ``task_tracking`` instead of a local
     counter that only ever rose;
  4. the dead by-agent ``claim_next_queued`` is replaced by ``claim_task``, a
     single CAS UPDATE that ``run_one_task`` actually calls.

What is NOT yet done is the part no unit test can supply: a real-stack run
proving a delegated task travels the whole chain on the deployed worker
(phase 2b-2 T7). Until that run, the flag stays false — advertising Delegate
over an unproven chain is the same silent footgun as before: the model gets
``status="queued"`` and the user gets nothing.

Off (default) means the tool is not advertised to the LLM AND ``execute()``
fail-closes with a clear message.
"""

from __future__ import annotations

from app.core.config import settings


def delegate_feature_enabled() -> bool:
    """True only when ``FEATURE_WORKFORCE_DELEGATE`` is explicitly enabled.

    Read from settings (config.yml / env / .env), not a bare ``os.getenv`` —
    so it resolves once at startup like every other FEATURE_* flag rather than
    changing under a running process.
    """
    return bool(settings.FEATURE_WORKFORCE_DELEGATE)


__all__ = ["delegate_feature_enabled"]
