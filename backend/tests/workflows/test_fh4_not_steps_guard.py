"""fh4 T2: the new helpers that run inside an existing workflow body or step
are plain async functions, never DBOS steps. A step added to a workflow body
shifts the step ids of in-flight workflows across a deploy."""

import inspect

import pytest

from app.services.workforce import subagent_delivery
from app.workflows import agent_runs_sweeper

pytestmark = pytest.mark.unit

_PLAIN = (
    agent_runs_sweeper._wake_for_reaped_child,
    agent_runs_sweeper.alert_unclaimed_subagent_results,
    subagent_delivery.deliver_subagent_result,
    subagent_delivery.terminal_run_for_task,
    subagent_delivery.envelope_from_prior_run,
)


@pytest.mark.parametrize("fn", _PLAIN, ids=lambda f: f.__name__)
def test_new_helpers_are_not_dbos_steps(fn):
    assert not hasattr(fn, "dbos_function_name")
    assert inspect.unwrap(fn) is fn
