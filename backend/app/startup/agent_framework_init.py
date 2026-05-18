"""Agent framework primitives — LifecycleBus, LaneQueue, BoundsRegistry,
ContextEngineRegistry, ModelHealthRegistry, RootAbortRegistry, AgentMetrics,
PrometheusPusher, HookRegistry, bounds heartbeat.

Plus chat ContextEngine registration and worker bounds self-registration
(D10-1 / D10-14 / Sprint 5+5.5 / Wave G / Wave I).
"""

import asyncio
import os
import socket

from fastapi import FastAPI
from loguru import logger

from app.services.infra import dbos_orchestrator


async def install_agent_primitives(app: FastAPI) -> None:
    try:
        from app.agent_framework import (
            BoundsRegistry,
            ContextEngineRegistry,
            LaneQueue,
            LifecycleBus,
            ModelHealthRegistry,
        )

        app.state.lifecycle_bus = LifecycleBus()
        app.state.lane_queue = LaneQueue()
        app.state.model_health = ModelHealthRegistry()

        from app.agent_framework.root_abort_registry import RootAbortRegistry

        app.state.root_abort_registry = RootAbortRegistry()

        from app.agent_framework.telemetry import AgentMetrics

        app.state.agent_metrics = AgentMetrics()

        await _install_prometheus_pusher(app)
        _install_hook_registry(app)

        app.state.context_engines = ContextEngineRegistry()
        _register_chat_context_engine(app)

        app.state.bounds_registry = BoundsRegistry()
        app.state.bounds_self_id = None

        process_role = app.state.process_role
        if process_role.runs_dbos_workers:
            await _register_self_bounds(app)

        dbos_orchestrator.set_bounds_registry(app.state.bounds_registry)

        logger.info(
            "Agent framework primitives ready "
            "(LifecycleBus + LaneQueue + BoundsRegistry + ContextEngineRegistry)"
        )
    except Exception as e:
        logger.warning(f"Agent framework primitive setup failed: {e}")


async def _install_prometheus_pusher(app: FastAPI) -> None:
    """D10-14: Prometheus pushgateway agent. Only fires when env is set."""
    try:
        from app.agent_framework.prometheus_pusher import from_env as _pp_from_env

        pusher = _pp_from_env(app.state.agent_metrics)
        if pusher is not None:
            await pusher.start()
            app.state.prometheus_pusher = pusher
    except Exception as pp_exc:
        logger.warning(f"D10-14 pusher start failed: {pp_exc}")


def _install_hook_registry(app: FastAPI) -> None:
    """Wave G (G2): per-process HookRegistry seeded with legacy hooks.

    BudgetGuard takes per-run constructor args, so it's instantiated by
    the AgentRunner caller — not registered globally here.
    """
    try:
        from app.agent_framework import HookRegistry, wrap_legacy_post
        from app.services.infra.hooks.cost_auditor import CostAuditorHook
        from app.services.infra.hooks.memory_harvester import MemoryHarvesterHook

        hook_registry = HookRegistry()
        try:
            hook_registry.register(wrap_legacy_post(CostAuditorHook()))
        except Exception as cae:
            logger.warning(f"hook register CostAuditor failed: {cae}")
        try:
            hook_registry.register(wrap_legacy_post(MemoryHarvesterHook()))
        except Exception as mhe:
            logger.warning(f"hook register MemoryHarvester failed: {mhe}")
        app.state.hook_registry = hook_registry
        logger.info(f"HookRegistry seeded with {len(hook_registry)} legacy hooks")
    except Exception as he:
        logger.warning(f"HookRegistry seed failed: {he}")


def _register_chat_context_engine(app: FastAPI) -> None:
    """Sprint 6.5: register the chat context engine.

    Search / Storyboard register their own engines from feature modules.
    """
    try:
        from app.services.ai.chat.chat_context_engine import ChatContextEngine

        app.state.context_engines.register(ChatContextEngine())
        logger.info("ContextEngine registered: chat")
    except Exception as ce_exc:
        logger.warning(f"ChatContextEngine registration failed: {ce_exc}")


async def _register_self_bounds(app: FastAPI) -> None:
    """Worker self-registers REAL inventory (workflow names, agent slugs,
    providers) so dispatch_gate can fail-fast for jobs no live worker
    can handle.
    """
    try:
        from app.agent_framework import BoundsAdvertisement
        from app.agent_framework.bounds_inventory import (
            inventory_agent_slugs,
            inventory_providers,
            inventory_workflow_names,
        )
        from app.core.config import settings
        from app.repositories.agent_repository import AgentRepository

        worker_id = f"{socket.gethostname()}-pid{os.getpid()}"

        # Re-import locally so the import lives in this scope (CPython's
        # compile-time symbol table means names imported in conditional
        # blocks are scope-local — see issue G2-FIX).
        workflow_names: frozenset[str] = frozenset()
        try:
            from app import workflows as _wf

            workflow_names = inventory_workflow_names(_wf)
        except Exception as inv_exc:
            logger.warning(f"Bounds: workflow inventory failed: {inv_exc}")

        agent_slugs = await inventory_agent_slugs(AgentRepository())
        providers = inventory_providers(settings)

        self_bound = BoundsAdvertisement(
            worker_id=worker_id,
            role=app.state.process_role.value,
            workflows=workflow_names,
            agents=agent_slugs,
            providers=providers,
        )
        app.state.bounds_registry.register(self_bound)
        app.state.bounds_self_id = worker_id
        logger.info(
            f"Bounds: self-registered worker_id={worker_id} "
            f"(workflows={len(workflow_names)} agents={len(agent_slugs)} "
            f"providers={sorted(providers)})"
        )
    except Exception as e:
        logger.warning(f"Bounds self-registration failed: {e}")


def install_bounds_heartbeat(app: FastAPI) -> None:
    """Sprint 5.5 + P0-3: 30s heartbeat so the registry's stale-prune
    (90s default) doesn't garbage-collect us. Re-registers from cached
    bound if pruned anyway (clock skew, registry rebuild).

    Only runs on processes that registered themselves (workers).
    """
    app.state.bounds_heartbeat_task = None
    if not getattr(app.state, "bounds_self_id", None):
        return

    cached_bound = next(
        (
            b
            for b in app.state.bounds_registry.live_bounds()
            if b.worker_id == app.state.bounds_self_id
        ),
        None,
    )
    app.state.bounds_self_bound = cached_bound

    async def _heartbeat() -> None:
        wid = app.state.bounds_self_id
        while True:
            try:
                await asyncio.sleep(30.0)
                if not app.state.bounds_registry.heartbeat(wid):
                    bound = app.state.bounds_self_bound
                    if bound is not None:
                        app.state.bounds_registry.register(bound)
                        logger.warning(
                            f"Bounds heartbeat: {wid} was pruned; "
                            "re-registered from cached bound"
                        )
                    else:
                        logger.error(
                            f"Bounds heartbeat: {wid} pruned AND no "
                            "cached bound to re-register from"
                        )
            except asyncio.CancelledError:
                break
            except Exception as hb_exc:
                logger.warning(f"Bounds heartbeat tick failed: {hb_exc}")

    app.state.bounds_heartbeat_task = asyncio.create_task(
        _heartbeat(), name="bounds-heartbeat"
    )
    logger.info("Bounds heartbeat task started (30s tick)")


async def stop_bounds_heartbeat(app: FastAPI) -> None:
    task = getattr(app.state, "bounds_heartbeat_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (BaseException,):  # noqa: BLE001 — cancellation is expected
        pass
    if getattr(app.state, "bounds_self_id", None):
        try:
            app.state.bounds_registry.unregister(app.state.bounds_self_id)
            logger.info("Bounds: unregistered self on shutdown")
        except Exception as ub_exc:
            logger.warning(f"Bounds unregister failed: {ub_exc}")


async def stop_prometheus_pusher(app: FastAPI) -> None:
    pusher = getattr(app.state, "prometheus_pusher", None)
    if pusher is None:
        return
    try:
        await pusher.stop()
        logger.info("D10-14 PrometheusPusher stopped")
    except Exception as e:
        logger.warning(f"PrometheusPusher stop raised {e!r}")
