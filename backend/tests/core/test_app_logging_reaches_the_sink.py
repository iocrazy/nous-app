"""Our own stdlib-logging modules must reach the database sink.

`InterceptHandler` bridges stdlib logging into loguru — but it is attached to
a fixed list of THIRD-PARTY logger names (uvicorn, celery, httpx, httpcore).
Our own modules that use `logging.getLogger(__name__)` were never on it, so
their output goes nowhere: not to loguru, not to `db_log_sink`, not to
`application_logs`.

Ground truth, production, 2026-08-23 — `application_logs` holds 2.8M rows:

    agent_runner            stdlib   0 rows
    llm_fallback_chain      stdlib   0 rows
    llm_compactor           stdlib   0 rows
    agent_worker            stdlib   0 rows
    ai_library_chat_service loguru  63 rows   ← positive control
    browser_client          loguru 209 rows   ← positive control

The whole agent hot path — the runner, the fallback chain, the compactor, the
workforce worker — has no logs in the database at all. Not sparse: absent.
The controls prove the query works, so those zeros are evidence.

The fix is deliberately narrow: bridge the `app` namespace, not the root
logger. Root would sweep in every third-party library that logs (sqlalchemy,
asyncio, botocore …) and turn a 2.8M-row table into a firehose — trading a
blind spot for a flood. `app` captures exactly our ~168 statements.
"""

import logging

import pytest

from app.core.utils import bridge_app_logging_to_loguru


@pytest.fixture
def clean_app_logger():
    lg = logging.getLogger("app")
    before = (list(lg.handlers), lg.level, lg.propagate)
    yield lg
    lg.handlers, lg.level, lg.propagate = before


@pytest.mark.unit
def test_our_modules_reach_loguru_after_bridging(clean_app_logger, monkeypatch):
    from loguru import logger as loguru_logger

    seen: list[str] = []
    sink_id = loguru_logger.add(lambda m: seen.append(str(m)), level="INFO")
    try:
        bridge_app_logging_to_loguru()
        logging.getLogger("app.services.ai.runner.agent_runner").warning("hot path")
        assert any("hot path" in s for s in seen), seen
    finally:
        loguru_logger.remove(sink_id)


@pytest.mark.unit
def test_the_test_would_fail_without_the_bridge(clean_app_logger):
    """A guard that passes because loguru catches everything anyway proves
    nothing. Strip the handlers and confirm the message does NOT arrive."""
    from loguru import logger as loguru_logger

    lg = logging.getLogger("app")
    lg.handlers = []
    lg.propagate = False

    seen: list[str] = []
    sink_id = loguru_logger.add(lambda m: seen.append(str(m)), level="INFO")
    try:
        logging.getLogger("app.services.ai.runner.agent_runner").warning("unbridged")
        assert not any("unbridged" in s for s in seen), (
            "the message arrived without the bridge — this test cannot detect "
            "the defect it exists for"
        )
    finally:
        loguru_logger.remove(sink_id)


@pytest.mark.unit
def test_third_party_loggers_are_not_swept_in(clean_app_logger):
    """`app`, not root. Bridging root would pull in sqlalchemy/asyncio/botocore
    and drown a 2.8M-row table."""
    bridge_app_logging_to_loguru()
    root = logging.getLogger()
    assert not any(
        type(h).__name__ == "InterceptHandler" for h in root.handlers
    ), "the bridge was attached to the ROOT logger — that is the flood case"


@pytest.mark.unit
def test_bridging_twice_does_not_duplicate_handlers(clean_app_logger):
    """Startup can run more than once (reload, tests, worker fork). Duplicate
    handlers mean every line lands in the database twice."""
    bridge_app_logging_to_loguru()
    bridge_app_logging_to_loguru()
    intercepts = [
        h
        for h in logging.getLogger("app").handlers
        if type(h).__name__ == "InterceptHandler"
    ]
    assert len(intercepts) == 1, intercepts


@pytest.mark.unit
def test_propagation_is_left_on_so_standard_capture_still_works(clean_app_logger):
    """This assertion started life inverted, and the suite corrected it.

    Turning propagation off is the reflex — "don't let the same line reach
    root handlers too". But this app never configures root handlers (root's
    handler list is empty after setup), so there is nothing to double up,
    while five existing tests capture `app.*` records through pytest's
    `caplog`, which works BY propagating to root. The first version of this
    bridge switched propagation off and broke all five for a benefit that
    does not exist here.
    """
    bridge_app_logging_to_loguru()
    assert logging.getLogger("app").propagate is True

    root = logging.getLogger()
    assert not root.handlers or all(
        type(h).__name__ != "InterceptHandler" for h in root.handlers
    ), "root should not carry our bridge — see the third-party flood test"
