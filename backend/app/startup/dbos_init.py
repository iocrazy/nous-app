"""DBOS orchestrator + process role + cleanup handlers (PR-D2.2 / D5 / D10-7).

Always launches DBOS (even on gateway-only roles) because the SDK requires
`_launch()` to populate `_sys_db_field` for dispatch. Per PR #172 post-mortem:
gating launch behind role caused 30s-2min 500 windows on every gateway
restart.
"""

from fastapi import FastAPI
from loguru import logger

from app.agent_framework import role_from_env
from app.services.infra import dbos_orchestrator


def install_process_role(app: FastAPI) -> None:
    process_role = role_from_env()
    app.state.process_role = process_role
    logger.info(f"Process role: {process_role.value}")


def install_cleanup_handlers() -> None:
    """D10-7: install atexit + SIGINT/SIGTERM handlers to kill child procs.

    Defends against orphan children holding DB connections / file
    descriptors after pytest crash / dev script Ctrl-C.
    """
    try:
        from app.agent_framework.process_lifecycle import (
            install_cleanup_handlers as _install,
        )

        _install()
        logger.info("D10-7 process cleanup handlers installed")
    except Exception as plc_exc:
        logger.warning(f"D10-7 cleanup install failed: {plc_exc}")


def init_dbos(app: FastAPI) -> None:
    """Init + launch DBOS orchestrator (registers @DBOS.workflow decorators).

    `from app import workflows` (not `import app.workflows`) so the `app`
    parameter isn't shadowed by a local module binding. Failure is
    non-fatal; only DBOS-routed task_types degrade.

    The gateway role launches DBOS (dispatch needs `_sys_db`) but consumes no
    user queues — `runs_dbos_workers` is False for gateway, so workflows only
    execute on the worker. See
    docs/superpowers/plans/2026-06-02-gateway-enqueue-only.md.
    """
    try:
        # Stable per-role executor id isolates the DBOS recovery path (which
        # ignores listen_queues) so a gateway restart can't re-run the worker's
        # in-flight workflows. Role name is stable across restarts; container
        # hostname is NOT (it changes on recreate), so don't use it.
        dbos_orchestrator.init_dbos(executor_id=app.state.process_role.value)
        if dbos_orchestrator.is_enabled():
            from app import workflows  # noqa: F401 — registers @DBOS decorators

            consume = app.state.process_role.runs_dbos_workers
            dbos_orchestrator.launch_dbos(consume_queues=consume)
            logger.info(
                f"DBOS orchestrator launched (role={app.state.process_role.value}, "
                f"consume_queues={consume})"
            )
    except Exception as e:
        logger.error(
            f"DBOS orchestrator startup failed: {e!r} — continuing without DBOS"
        )


async def shutdown_dbos() -> None:
    try:
        dbos_orchestrator.shutdown_dbos()
    except Exception as e:
        logger.warning(f"DBOS shutdown raised {e!r}")
