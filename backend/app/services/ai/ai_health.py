"""AI capability health — make the capability→agent→model→provider→key
chain legible to users.

The mapping that decides which model+key each AI feature uses is spread
across task_assignment, ai_agents.model, and ai_providers — invisible in
the UI, and a broken key fails silently (the ark-key visual-analysis
outage that motivated this). This module resolves each capability through
the EXACT runtime resolver (resolve_task_ai_config) and reports an
actionable status so the Settings panel can show a status board.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.ai.ai_health_runtime import fetch_runtime_summary
from app.services.ai.providers.ai_provider_helpers import (
    get_ai_settings,
    resolve_embedding_ai_config,
    resolve_scorer_config,
    resolve_task_ai_config,
    resolve_transcription_config,
)

logger = logging.getLogger(__name__)

# (task_assignment key, default agent slug, human label, needs a vision model?,
#  task_tracking task_type or None). The task_type links a capability to its
# workflow runs so the runtime layer can flag a *currently failing* feature
# even when its static config looks healthy. Capabilities that run inline
# (no DBOS workflow → no task_tracking row) carry None and get config-only
# health, as before. visual_analysis (ai_extract) is the capability that
# motivated this — the ark-key AccessDenied outage left a healthy-looking
# board while every call failed.
_CAPABILITIES: list[tuple[str, str, str, bool, str | None]] = [
    ("summarization", "summarize", "Rewrite (summary)", False, None),
    ("visual_analysis", "analyze", "Analyze (visual)", True, "ai_extract"),
    ("caption", "caption", "Image → Prompt", True, None),
    # ⚠️ task_assignment key 是 "classification"(与 workflow / TASK_MODULES /
    # 前端 types.ts 一致),default agent slug 才是 "classify" —— 两个命名空间。
    # 2026-08-20 审查 F4:这里此前两个都写成 "classify",于是板子读不到用户真正
    # 指派的分类 agent(显示默认那个),而 get_module_governance("classify") 不在
    # TASK_MODULES 里会落到 chat 分支,分类的 governance 锁定在板子上不可见。
    ("classification", "classify", "Auto Tag", True, None),
    ("translation", "translate", "Translation", False, None),
]

# Substrings that mark a model as vision-capable. Conservative: the
# capability is only flagged ``not_vision`` when NONE of these match, so a
# new VL model naming scheme degrades to "ok" (no false alarm) rather than
# a false "broken".
_VISION_HINTS = (
    "vl",
    "vision",
    "-v3",
    "gpt-4o",
    "gpt-4-turbo",
    "claude",
    "doubao-seed",
)


def _has_key(providers: dict, provider_key: str) -> bool:
    raw = (providers.get(provider_key) or {}).get("api_key")
    if isinstance(raw, list):
        return any(isinstance(k, str) and k.strip() for k in raw)
    return isinstance(raw, str) and bool(raw.strip())


def _looks_vision(model: str) -> bool:
    m = (model or "").lower()
    return any(h in m for h in _VISION_HINTS)


def _evaluate(
    *,
    provider_key: str,
    model: str,
    needs_vision: bool,
    providers: dict,
    origin: str = "",
    resolved_config: dict | None = None,
) -> tuple[str, str]:
    if not model:
        return (
            "no_model",
            "The assigned agent has no model set — pick one in AI Library.",
        )
    # An admin-managed resolution (governance lock / platform catalog) carries
    # its own credentials — the user's BYOK cards are irrelevant to it, and a
    # prefix the factory doesn't know (e.g. a self-hosted embedding model) is
    # fine because these paths dial base_url directly.
    if origin in ("governance", "platform"):
        if not ((resolved_config or {}).get("api_key") or "").strip():
            return (
                "no_key",
                "This capability is admin-managed but its platform config has "
                "no API key — fix it in Admin → AI Models / module settings.",
            )
        if needs_vision and not _looks_vision(model):
            return (
                "not_vision",
                f"'{model}' is a text-only model but this feature needs to see "
                "images. Switch to a vision model (e.g. a *-VL-* model).",
            )
        return "ok", ""
    if not provider_key:
        return (
            "unknown_provider",
            f"Model '{model}' maps to no known provider — check the agent's model.",
        )
    if not _has_key(providers, provider_key):
        return (
            "no_key",
            f"No API key for '{provider_key}'. Add it in the {provider_key} provider "
            "card above, or point this capability's agent at a provider you've configured.",
        )
    if needs_vision and not _looks_vision(model):
        return (
            "not_vision",
            f"'{model}' is a text-only model but this feature needs to see images. "
            "Switch the agent to a vision model (e.g. a *-VL-* model).",
        )
    return "ok", ""


def _apply_runtime(
    status: str, hint: str, *, model: str, provider: str, rt: dict[str, Any]
) -> tuple[str, str]:
    """Overlay runtime reality on a config-healthy capability.

    When the static config is ``ok`` but the most recent tracked run failed,
    the capability is *currently* broken (the model+key resolve, yet the call
    is rejected — e.g. AccessDenied). Surface it as ``runtime_failing`` with
    the real error. Config problems keep priority: they are the actionable
    root cause, so a runtime blip never masks a missing key.
    """
    if status != "ok" or not rt.get("latest_failed"):
        return status, hint
    err = (rt.get("last_error") or "").strip() or "see Task Center for details"
    return (
        "runtime_failing",
        f"Latest run failed: {err}. '{model}' and the {provider} key resolve, "
        "but the call is being rejected — verify the key has access to this model.",
    )


async def get_capability_health(user_id: str) -> list[dict[str, Any]]:
    """One status row per AI capability. Never raises — a capability whose
    resolution crashes becomes an ``error`` row, its siblings unaffected."""
    ai_settings = await get_ai_settings(user_id) if user_id else {}
    providers = ai_settings.get("ai_providers") or {}
    assignments = ai_settings.get("task_assignment") or {}

    task_types = [tt for *_rest, tt in _CAPABILITIES if tt]
    runtime = await fetch_runtime_summary(user_id, task_types) if user_id else {}

    rows: list[dict[str, Any]] = []
    for task_key, default_slug, label, needs_vision, task_type in _CAPABILITIES:
        assigned = bool((assignments.get(task_key) or "").strip())
        try:
            # EVERY capability in this table — summarization included as of the
            # 2026-08-20 收口 — resolves through the one agent-config resolver
            # the real workflows call. Summarization used to need a special
            # case here because its workflow scanned a hardcoded provider
            # priority instead of reading an agent row; that path is gone, and
            # with it the risk that the board reports a chain the feature
            # doesn't use (the "Resolve through the TRUE path" rule that
            # motivated the special case is now satisfied by uniformity).
            cfg = await resolve_task_ai_config(user_id, task_key, default_slug)
            provider_key, model, slug = cfg.provider_key, cfg.model, cfg.agent_slug
            origin = cfg.origin
            resolved_config = cfg.provider_config
            status, hint = _evaluate(
                provider_key=provider_key,
                model=model,
                needs_vision=needs_vision,
                providers=providers,
                origin=origin,
                resolved_config=resolved_config,
            )
            row = {
                "capability": task_key,
                "label": label,
                "agent_slug": slug,
                "assigned": assigned,
                "model": model,
                "provider": provider_key,
                "needs_vision": needs_vision,
                "origin": origin,
                "status": status,
                "hint": hint,
            }
            if task_type:
                rt = runtime.get(task_type) or {}
                status, hint = _apply_runtime(
                    status, hint, model=model, provider=provider_key, rt=rt
                )
                row.update(
                    {
                        "status": status,
                        "hint": hint,
                        "task_type": task_type,
                        "recent_runs": rt.get("recent_runs", 0),
                        "recent_failures": rt.get("recent_failures", 0),
                        "last_error": rt.get("last_error", ""),
                    }
                )
            rows.append(row)
        except Exception:  # noqa: BLE001 — one bad capability must not sink the board
            logger.exception("[ai_health] capability %s resolution failed", task_key)
            rows.append(
                {
                    "capability": task_key,
                    "label": label,
                    "agent_slug": "",
                    "assigned": assigned,
                    "model": "",
                    "provider": "",
                    "needs_vision": needs_vision,
                    "status": "error",
                    "hint": "Could not resolve this capability — see server logs.",
                }
            )
    rows.extend(await _system_capability_rows(user_id, ai_settings))
    await _overlay_probe_health(rows, ai_settings)
    return rows


async def _overlay_probe_health(rows: list[dict[str, Any]], ai_settings: dict) -> None:
    """Overlay LIVE key verdicts onto config-healthy rows.

    Static resolution proves which model+key a chain WILL dial, not that the
    key still works — an exhausted/402 key resolves fine and the board reads
    green while every call fails. Two verdict sources already exist in the DB;
    join them instead of dialing anything new:

      - platform/governance rows → the admin probe board
        (``mediahub_models.last_test_status`` / ``last_test_detail``). For a
        governance row the admin's manual key usually IS the catalog key, so
        the probe verdict is the best available signal (hint says so).
      - byok rows → the user's persisted test-connection verdicts
        (``settings_json.ai_provider_health.<provider>`` — #973). Never
        tested ≠ failing: absence stays green.

    Config problems keep priority (same philosophy as the runtime overlay):
    only ``ok`` rows are overlaid. Best-effort — a broken lookup leaves the
    row untouched.
    """
    provider_health = ai_settings.get("ai_provider_health") or {}

    # Batch the catalog lookups (dedup by model) — the board renders on every
    # Settings visit, so avoid one query per row.
    catalog_models = {
        r["model"]
        for r in rows
        if r.get("status") == "ok"
        and r.get("origin") in ("platform", "governance")
        and r.get("model")
    }
    verdicts: dict[str, tuple[str, str]] = {}
    if catalog_models:
        try:
            from app.repositories.mediahub_model_repository import (
                get_mediahub_model_repository,
            )

            repo = get_mediahub_model_repository()
            for model in catalog_models:
                row = await repo.get_by_name(model)
                if not row:
                    row = await repo.get_by_actual_model(model)
                if row and (row.get("last_test_status") or "") == "fail":
                    verdicts[model] = (
                        "fail",
                        (row.get("last_test_detail") or "").strip(),
                    )
        except Exception as exc:  # noqa: BLE001 — overlay must not sink the board
            logger.warning("[ai_health] probe overlay lookup failed: %s", exc)
            return

    for r in rows:
        if r.get("status") != "ok":
            continue
        origin = r.get("origin") or ""
        if origin in ("platform", "governance"):
            verdict = verdicts.get(r.get("model") or "")
            if verdict:
                detail = verdict[1][:160] or "see Admin → AI Models"
                r["status"] = "probe_failing"
                r["hint"] = (
                    f"Latest platform probe of '{r['model']}' FAILED: {detail} "
                    "— the config resolves but calls will likely be rejected "
                    "(Admin → AI Models)."
                )
        elif origin == "byok":
            entry = provider_health.get(r.get("provider") or "") or {}
            if entry.get("status") == "fail":
                detail = (entry.get("detail") or "").strip()[:160]
                tested = (entry.get("tested_at") or "")[:10]
                r["status"] = "key_test_failed"
                r["hint"] = (
                    f"Your '{r['provider']}' key failed its last test"
                    f"{f' ({tested})' if tested else ''}: "
                    f"{detail or 'no detail recorded'} — re-test it in "
                    "Settings → AI Providers."
                )


def _resolved_row(
    *,
    capability: str,
    label: str,
    cfg: Any,
    providers: dict,
    hint_when_unconfigured: str,
) -> dict[str, Any]:
    """Build one board row from a ResolvedAIConfig-shaped resolver result.

    ``origin="env"`` with an empty model is the shared no-provider
    fall-through — surfaced as ``not_configured`` with a capability-specific
    pointer instead of the generic agent hints."""
    if not (cfg.model or "").strip():
        status, hint = "not_configured", hint_when_unconfigured
    else:
        status, hint = _evaluate(
            provider_key=cfg.provider_key,
            model=cfg.model,
            needs_vision=False,
            providers=providers,
            origin=cfg.origin,
            resolved_config=cfg.provider_config,
        )
    return {
        "capability": capability,
        "label": label,
        "agent_slug": cfg.agent_slug,
        "assigned": False,
        "model": cfg.model,
        "provider": cfg.provider_key,
        "needs_vision": False,
        "origin": cfg.origin,
        "status": status,
        "hint": hint,
    }


async def _system_capability_rows(
    user_id: str, ai_settings: dict
) -> list[dict[str, Any]]:
    """Board rows for the capabilities OUTSIDE the agent-task loop: chat,
    transcription, topic scorer, embedding, and the maintenance tier.
    Each resolves through its real runtime resolver; failures degrade to an
    ``error`` row (never sink the board)."""
    providers = ai_settings.get("ai_providers") or {}
    rows: list[dict[str, Any]] = []

    # ── transcription (ASR) — user-scoped typed resolver ────────────────
    try:
        cfg = await resolve_transcription_config(
            user_id, settings_json={"ai_settings": ai_settings}
        )
        # model may be a "provider:model" descriptor (the transcription
        # picker's storage format) or "" under governance — show it as-is;
        # under governance the real model rides in provider_config.
        display = cfg.model or (cfg.provider_config or {}).get("model") or ""
        shown = ResolvedAIConfigView(cfg, display)
        rows.append(
            _resolved_row(
                capability="transcription",
                label="Transcribe (ASR)",
                cfg=shown,
                providers=providers,
                hint_when_unconfigured="Pick a transcription provider in "
                "Settings → AI (task assignment), or ask the admin to enable "
                "a platform ASR model.",
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception("[ai_health] transcription resolution failed")
        rows.append(_error_row("transcription", "Transcribe (ASR)"))

    # ── chat — credential surface only (the agent owns the model) ───────
    try:
        from app.services.ai.governance.ai_governance import (
            get_module_governance,
            get_platform_ai_providers,
        )

        gov = await get_module_governance("chat")
        platform_providers = await get_platform_ai_providers()
        merged = {**platform_providers, **(providers if gov.allowed else {})}
        any_key = any(_has_key(merged, k) for k in merged)
        status = "ok" if any_key else "not_configured"
        hint = (
            ""
            if any_key
            else "No chat credentials anywhere: add a provider key in "
            "Settings → AI Providers, or enable a platform model "
            "(Admin → AI Models)."
        )
        rows.append(
            {
                "capability": "chat",
                "label": "Chat (agents)",
                "agent_slug": "",
                "assigned": False,
                "model": "",  # agent-owned, varies per conversation
                "provider": "",
                "needs_vision": False,
                "origin": "governance" if not gov.allowed else "byok",
                "status": status,
                "hint": hint,
            }
        )
    except Exception:  # noqa: BLE001
        logger.exception("[ai_health] chat resolution failed")
        rows.append(_error_row("chat", "Chat (agents)"))

    # ── topic scorer — system capability, admin-configured ──────────────
    try:
        rows.append(
            _resolved_row(
                capability="topic_scorer",
                label="Topic Scorer",
                cfg=await resolve_scorer_config(),
                providers=providers,
                hint_when_unconfigured="No scorer model: set one in Admin → "
                "module settings, or enable a platform LLM (Admin → AI Models).",
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception("[ai_health] topic_scorer resolution failed")
        rows.append(_error_row("topic_scorer", "Topic Scorer"))

    # ── embedding — search / memory vectors ──────────────────────────────
    try:
        rows.append(
            _resolved_row(
                capability="embedding",
                label="Embedding (search/memory)",
                cfg=await resolve_embedding_ai_config(),
                providers=providers,
                hint_when_unconfigured="Embeddings are disabled: configure a "
                "model in Admin → module settings (embedding) or the memory "
                "stack's embedder config.",
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception("[ai_health] embedding resolution failed")
        rows.append(_error_row("embedding", "Embedding (search/memory)"))

    # ── maintenance tier — compaction / session-memory / distillation ───
    try:
        from app.services.ai.providers.ai_provider_helpers import (
            get_maintenance_model,
            resolve_mediahub_model,
        )

        m = await get_maintenance_model()
        try:
            hit = await resolve_mediahub_model(m, "maintenance")
        except RuntimeError:
            hit = None  # found but disabled/gated → treat as unresolved
        if hit:
            _prov, pcfg, actual = hit
            rows.append(
                {
                    "capability": "maintenance",
                    "label": "Maintenance (compaction/memory)",
                    "agent_slug": "",
                    "assigned": False,
                    "model": actual,
                    "provider": _prov,
                    "needs_vision": False,
                    "origin": "platform",
                    "status": "ok" if (pcfg.get("api_key") or "").strip() else "no_key",
                    "hint": (
                        ""
                        if (pcfg.get("api_key") or "").strip()
                        else f"Catalog model '{m}' has no API key (Admin → AI Models)."
                    ),
                }
            )
        else:
            rows.append(
                {
                    "capability": "maintenance",
                    "label": "Maintenance (compaction/memory)",
                    "agent_slug": "",
                    "assigned": False,
                    "model": m,
                    "provider": "",
                    "needs_vision": False,
                    "origin": "platform",
                    "status": "not_configured",
                    "hint": f"Maintenance model '{m}' is not an enabled platform "
                    "catalog entry — fix system_settings.maintenance_llm_model "
                    "or enable the model in Admin → AI Models.",
                }
            )
    except Exception:  # noqa: BLE001
        logger.exception("[ai_health] maintenance resolution failed")
        rows.append(_error_row("maintenance", "Maintenance (compaction/memory)"))

    return rows


class ResolvedAIConfigView:
    """Tiny display adapter: same attribute surface as ResolvedAIConfig but
    with a substituted display model (transcription's governance path keeps
    the real model inside provider_config)."""

    def __init__(self, cfg: Any, display_model: str):
        self.provider_key = cfg.provider_key
        self.provider_config = cfg.provider_config
        self.model = display_model
        self.agent_slug = cfg.agent_slug
        self.origin = cfg.origin


def _error_row(capability: str, label: str) -> dict[str, Any]:
    return {
        "capability": capability,
        "label": label,
        "agent_slug": "",
        "assigned": False,
        "model": "",
        "provider": "",
        "needs_vision": False,
        "origin": "",
        "status": "error",
        "hint": "Could not resolve this capability — see server logs.",
    }


__all__ = ["get_capability_health"]
