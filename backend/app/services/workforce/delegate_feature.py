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

**The flag is ON as of 2026-09-10** (`config.yml`, PR #2221): the chain was
exercised locally end to end and the real-stack acceptance ran it far enough to
prove the tool reaches the model and refuses with a typed message when there is
no valid target. What that acceptance could NOT prove is a delegated task
travelling the whole chain on the deployed worker — production had zero
``ai_agents.persistent=true`` rows, so every call was refused before the inbox
(Task 7a defect 4, since fixed at the seed loader). That run is still owed.

Turning it back off is a real kill switch, not a no-op: the tool is no longer
advertised to the LLM, ``execute()`` fail-closes with a message the model can
act on, and the composer stops rendering ``<available_workers>`` altogether —
which also means the system-message prefix changes for every agent, so the
prompt cache is invalidated once in each direction (see
``app/services/ai/prompts/README.md``). It is safe to flip; it is not free.
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
