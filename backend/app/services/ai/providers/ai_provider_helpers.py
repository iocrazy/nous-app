"""Shared AI-provider config helpers — extracted from the legacy
`app.tasks.ai_tasks` + `analysis_tasks` (PR-D7 phase 3 cleanup).

Pure helpers (no Celery). Used by:
    - `app.workflows.analyze_l1` (resolve_analyze_provider step)

Each helper preserves the exact behaviour of its `_xxx` predecessor
in the legacy tasks module.

§2.4b async DB-hoist: the DB-reading helpers are now ``async def`` and
``await`` the repos directly. They were previously sync, bridging to the
async repos via a fresh-event-loop ``_run_async`` shim — a pattern that is
incompatible with the SQLAlchemy ORM (asyncpg connections are loop-bound)
and that caused a prod connection-leak (2026-05-22). The sole caller,
``analyze_l1.resolve_analyze_provider``, is an ``async @DBOS.step`` awaited
from the async ``analyze_l1_workflow``, so awaiting here runs on the
workflow's own loop — no bridge, ORM-safe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from loguru import logger


@dataclass(frozen=True)
class ResolvedAIConfig:
    """Typed result of :func:`resolve_task_ai_config`.

    Carries the same four values the legacy tuple return exposed
    (``provider_key`` / ``provider_config`` / ``model`` / ``agent_slug``)
    plus an ``origin`` tag identifying WHICH resolution branch produced the
    config:

      - ``"governance"`` — admin-locked module config (user path bypassed).
      - ``"platform"``  — a platform ``mediahub_models`` catalog entry, whether
        the user picked it (``nous:<model>``) or the assigned agent's model
        resolved through the gated catalog path.
      - ``"byok"``      — the user's own provider config, with a non-empty
        ``api_key``.
      - ``"env"``       — a BYOK-shaped result WITHOUT an api_key; the adapter
        factory falls back to env credentials.
    """

    provider_key: str
    provider_config: Dict[str, Any]
    model: str
    agent_slug: str
    origin: str  # "governance" | "platform" | "byok" | "env"
    # mig 155 的 ai_agents.fallback_models,仅 agent 行分支填充(spec
    # 2026-08-11-batch-llm-fallback §4);tuple 保持 frozen 语义。
    fallback_models: tuple[str, ...] = ()


async def get_ai_settings(user_id: str) -> dict:
    """Load user's AI settings from the database.

    ``ai_providers`` api_key values are REVEALED (decrypted) here — the sole
    read chokepoint feeding every task resolver in this module — so no
    downstream consumer (adapter factory, resolve_task_ai_config, ...) needs
    to know about the ``enc:v1:`` marker scheme. Fail-soft: an undecryptable
    marked value degrades to ``""`` (see ``secure_settings.reveal_byok_providers``),
    it never raises.
    """
    from app.core.secure_settings import reveal_byok_providers
    from app.repositories.user_settings_repository import UserSettingsRepository

    repo = UserSettingsRepository()
    settings = await repo.get_by_user_id(user_id)
    if not settings or not settings.get("settings_json"):
        return {}
    ai_settings = settings["settings_json"].get("ai_settings", {})
    if isinstance(ai_settings, dict) and "ai_providers" in ai_settings:
        ai_settings = {
            **ai_settings,
            # user_id is the row owner here (the repo query IS by user_id) —
            # the ownership binding embedded at encrypt time is verified.
            "ai_providers": reveal_byok_providers(
                ai_settings.get("ai_providers"), user_id=str(user_id)
            ),
        }
    return ai_settings


def get_provider_config(ai_settings: dict, provider_key: str) -> dict:
    """Extract provider config from AI settings."""
    providers = ai_settings.get("ai_providers", {})
    return providers.get(provider_key, {})


async def resolve_mediahub_model(
    model_name: str, module: str
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    """Resolve a model name against the platform ``mediahub_models`` registry.

    Full-table lookup by ``name`` (enabled + disabled), falling back to a
    lookup by ``actual_model`` (the raw upstream provider id) when the name
    lookup misses — some ``ai_agents.model`` rows store the raw Ark/provider
    model id (e.g. ``doubao-seed-2-0-lite-260428``) instead of the catalog
    ``name`` (e.g. ``mediahub-doubao-seed-2-0-lite``); without the fallback
    those agents fall through to an ordinary BYOK resolution that has no env
    key configured, producing a 401 from the upstream provider. ``name`` is
    tried first (unchanged behavior for correctly-named agents); the
    ``actual_model`` fallback only engages when the name lookup misses, and
    the two namespaces (``mediahub-*`` vs raw provider ids) never collide.
    Then:
      - found + enabled + nous allowed for ``module`` → return
        ``(actual_provider, {api_key, base_url, model, app_id}, actual_model)``
        — the platform config, ready for the existing adapter factory.
      - found + (disabled OR nous gated off) → **fail-closed** (RuntimeError);
        never silently fall back to a guessed BYOK provider.
      - not found → ``None`` (an ordinary BYOK model name like ``gpt-4o``).
    """
    from app.repositories.mediahub_model_repository import get_mediahub_model_repository
    from app.services.ai.governance.ai_governance import is_nous_allowed

    if not model_name:
        return None
    repo = get_mediahub_model_repository()
    row = await repo.get_by_name(model_name)
    if not row:
        row = await repo.get_by_actual_model(model_name)
    if not row:
        return None  # ordinary BYOK model name — leave the caller's path intact.

    if not await is_nous_allowed(module):
        raise RuntimeError(
            f"Platform model '{model_name}' is disabled for this feature."
        )
    if not row.get("is_enabled"):
        raise RuntimeError(f"Platform model '{model_name}' is no longer available.")

    provider_config: Dict[str, Any] = {
        "api_key": row.get("api_key", ""),
        "base_url": row.get("base_url") or "",
        "model": row["actual_model"],
        "app_id": row.get("app_id") or "",
    }
    return row["actual_provider"], provider_config, row["actual_model"]


async def resolve_platform_model(
    model_name: str,
) -> Optional[Tuple[str, Dict[str, Any], str]]:
    """Direct platform-catalog (``mediahub_models``) lookup, WITHOUT the user-facing
    nous gate.

    For ADMIN platform config: a module locked to / configured with a platform
    model is an admin decision, not a user pick, so ``nous.user_enabled`` /
    ``nous_allowed`` (which gate what USERS may pick) do not apply here.

    Returns ``(actual_provider, {api_key, base_url, model, app_id}, actual_model)``
    for an enabled catalog model; ``None`` when ``model_name`` isn't a catalog
    model (an ordinary manual model string); raises when found-but-disabled.

    Same ``name``-then-``actual_model`` fallback as :func:`resolve_mediahub_model`
    (see its docstring) — kept consistent so the admin/ungated path resolves an
    agent storing the raw provider id exactly like the gated user path does.
    """
    from app.repositories.mediahub_model_repository import get_mediahub_model_repository

    if not model_name:
        return None
    repo = get_mediahub_model_repository()
    row = await repo.get_by_name(model_name)
    if not row:
        row = await repo.get_by_actual_model(model_name)
    if not row:
        return None
    if not row.get("is_enabled"):
        raise RuntimeError(f"Platform model '{model_name}' is no longer available.")
    provider_config: Dict[str, Any] = {
        "api_key": row.get("api_key", ""),
        "base_url": row.get("base_url") or "",
        "model": row["actual_model"],
        "app_id": row.get("app_id") or "",
    }
    return row["actual_provider"], provider_config, row["actual_model"]


async def resolve_scorer_config() -> ResolvedAIConfig:
    """Report the topic scorer's FIRST-CHOICE resolution (A5, read-only).

    The scorer itself keeps its failover pool (``TopicScorer._resolve_candidates``
    walks governance → every enabled platform llm model); this resolver mirrors
    that order but reports only the first hit, for the health board:

      1. admin per-module governance (``ai_module.topic_scorer.*`` with model
         AND key) → ``origin="governance"``
      2. first enabled platform ``llm`` catalog model with base_url+key
         (nous gate honored) → ``origin="platform"``
      3. nothing configured → empty config, ``origin="env"`` (the no-provider
         fall-through convention shared with resolve_summarization_config).

    Never raises — resolution failures degrade to the not-configured shape.
    ``agent_slug`` is the scorer's fixed prompt agent (``topic-scorer``).
    """
    from app.services.ai.adapters.factory import provider_key_for_model
    from app.services.ai.governance.ai_governance import (
        get_module_governance,
        is_nous_allowed,
    )

    def _pk(model: str) -> str:
        try:
            return provider_key_for_model(model)
        except ValueError:
            return ""

    try:
        gov = await get_module_governance("topic_scorer")
        if gov.model and gov.api_key_present:
            return ResolvedAIConfig(
                provider_key=_pk(gov.model),
                provider_config={
                    "api_key": gov.api_key,
                    "base_url": gov.base_url,
                    "model": gov.model,
                    "app_id": "",
                },
                model=gov.model,
                agent_slug="topic-scorer",
                origin="governance",
            )
        if await is_nous_allowed("topic_scorer"):
            from app.repositories.mediahub_model_repository import (
                get_mediahub_model_repository,
            )

            repo = get_mediahub_model_repository()
            for m in await repo.list_enabled("llm"):
                full = await repo.get_by_name(m["name"])
                if full and full.get("base_url") and full.get("api_key"):
                    return ResolvedAIConfig(
                        provider_key=_pk(full["actual_model"]),
                        provider_config={
                            "api_key": full["api_key"],
                            "base_url": full["base_url"],
                            "model": full["actual_model"],
                            "app_id": full.get("app_id") or "",
                        },
                        model=full["actual_model"],
                        agent_slug="topic-scorer",
                        origin="platform",
                    )
    except Exception as exc:  # noqa: BLE001 — health reporting must not raise
        logger.warning(f"[resolve_scorer_config] degraded to not-configured: {exc}")

    return ResolvedAIConfig(
        provider_key="",
        provider_config={"api_key": "", "base_url": "", "model": "", "app_id": ""},
        model="",
        agent_slug="topic-scorer",
        origin="env",
    )


async def resolve_embedding_ai_config() -> ResolvedAIConfig:
    """Typed wrapper over :func:`resolve_embedding_config` (A5, read-only).

    Translates the embedder's own three-branch resolution (catalog →
    admin-manual → legacy ``graph_embedder_*``) into the shared
    :class:`ResolvedAIConfig` shape: catalog hit → ``origin="platform"``,
    either admin-config branch → ``origin="governance"``, embeddings
    disabled (None) → empty config with ``origin="env"`` (the shared
    no-provider convention). Never raises. ``agent_slug`` is ``""`` —
    embedding composes no agent prompt.
    """
    from app.services.ai.adapters.factory import provider_key_for_model
    from app.services.ai.providers.embedding_config import resolve_embedding_config

    try:
        cfg = await resolve_embedding_config()
    except Exception as exc:  # noqa: BLE001 — health reporting must not raise
        logger.warning(f"[resolve_embedding_ai_config] read failed: {exc}")
        cfg = None
    if cfg is None:
        return ResolvedAIConfig(
            provider_key="",
            provider_config={"api_key": "", "base_url": "", "model": "", "app_id": ""},
            model="",
            agent_slug="",
            origin="env",
        )
    try:
        provider_key = provider_key_for_model(cfg.model)
    except ValueError:
        provider_key = ""
    return ResolvedAIConfig(
        provider_key=provider_key,
        provider_config={
            "api_key": cfg.api_key,
            "base_url": cfg.base_url,
            "model": cfg.model,
            "app_id": "",
        },
        model=cfg.model,
        agent_slug="",
        origin=cfg.source or "governance",
    )


# Built-in maintenance calls (context compaction, session-memory notes,
# agent-memory promotion/consolidation) need a cheap default model when the
# primary path gives them nothing. Credentials are DB-only, so this default
# MUST name a platform catalog entry — the old hardcoded "qwen-turbo" /
# "claude-haiku-4-5" fallbacks resolve to nothing and silently degrade the
# whole maintenance tier. Admin-overridable via
# ``system_settings.maintenance_llm_model``.
DEFAULT_MAINTENANCE_MODEL = "mediahub-doubao-seed-2-0-lite"


async def get_maintenance_model() -> str:
    """The catalog model maintenance/utility LLM calls fall back to.

    ``system_settings.maintenance_llm_model`` (admin-set, DB) →
    :data:`DEFAULT_MAINTENANCE_MODEL`. Never raises — a broken settings
    read degrades to the default.
    """
    from app.services.ai.governance.ai_governance import _read_raw

    try:
        value = await _read_raw("maintenance_llm_model")
    except Exception:  # noqa: BLE001 — maintenance tier must not break callers
        value = None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEFAULT_MAINTENANCE_MODEL


async def resolve_db_adapter(
    model: str,
    module: str,
    user_provider_config: Optional[Dict[str, Any]] = None,
):
    """DB-first adapter resolution (铁律 2026-07-07: LLM credentials never
    come from env). Order:

      1. platform ``mediahub_models`` catalog (admin-managed) — hit swaps the
         model for ``actual_model`` and injects the platform key/base_url;
      2. user BYOK (``user_provider_config`` = the user's ``ai_providers``);
      3. neither → :class:`ProviderNotConfiguredError` from the factory.

    Returns an :class:`AIAdapter`. A catalog hit dispatches on the row's
    admin-named ``actual_provider`` (``resolve_provider_key``) — NEVER a
    prefix guess on ``actual_model``, which raises for models like
    ``qwen3-6-35b`` (2026-07-12 real-machine bug: row green in the admin
    health panel, dead in canvas). The final fallback is the
    OpenAI-compatible adapter — the same base_url + /chat/completions
    contract the health probe validates against this row.
    """
    from app.services.ai.adapters.factory import (
        get_adapter_for_key,
        get_adapter_for_user,
        resolve_provider_key,
    )

    hit = await resolve_mediahub_model(model, module)
    if hit:
        actual_provider, cfg, actual_model = hit
        creds = {"api_key": cfg["api_key"], "base_url": cfg["base_url"]}
        provider_key = resolve_provider_key(actual_provider, actual_model)
        return get_adapter_for_key(provider_key, actual_model, {provider_key: creds})

    return get_adapter_for_user(model, user_provider_config or {}, None)


DEFAULT_ANALYZE_AGENT_SLUG = "analyze"
DEFAULT_TRANSLATE_AGENT_SLUG = "translate"
DEFAULT_CAPTION_AGENT_SLUG = "caption"
DEFAULT_CLASSIFY_AGENT_SLUG = "classify"
DEFAULT_SCRIPT_AGENT_SLUG = "script_ai"


def _byok_origin(provider_config: Dict[str, Any]) -> str:
    """Classify a BYOK-shaped result: ``"byok"`` when the config carries a
    non-empty ``api_key``, else ``"env"`` (the adapter factory falls back to
    env credentials when no user key is present)."""
    return "byok" if (provider_config.get("api_key") or "").strip() else "env"


def _summary_model_from_provider(
    provider_key: str, provider_cfg: Dict[str, Any]
) -> str:
    """The provider-card field that actually holds a SUMMARY model.

    Every provider except ``openai`` exposes ONE model chip list in Settings →
    AI, and it writes ``selected_model`` — so for those the summary model and
    ``selected_model`` are the same field by construction.

    ``openai`` is the exception: its card renders THREE dropdowns writing three
    different keys (Whisper Model → ``selected_model``, Summary Model →
    ``summary_model``, Analysis Model → ``analysis_model``, see
    ``frontend/components/AISettings.tsx``). Reading ``selected_model`` for
    openai therefore hands summarization whatever the user last picked in the
    *Whisper* dropdown — a ``whisper-1``-class id posted to
    ``/v1/chat/completions``, which fails every time. So openai reads
    ``summary_model`` first.

    It still falls back to ``selected_model`` afterwards, because rows written
    before that dropdown existed keep a CHAT model there — but only when the
    value is not itself an ASR pick. Note the openai card never writes
    ``summary_model`` unless the user opens that dropdown (it *displays*
    ``gpt-4o-mini`` as a placeholder without storing it), so "whisper in
    ``selected_model``, no ``summary_model`` at all" is the ordinary shape of a
    user who only ever touched the Whisper dropdown — an unguarded fallback
    would resolve straight back to the broken model. The ASR test is the same
    one the card itself uses to decide whether ``selected_model`` belongs to its
    Whisper dropdown (``selected_model?.startsWith('whisper')``), deliberately
    narrower than the frontend's general non-chat heuristic
    (``frontend/utils/nonChatModel.ts``): this is one provider's known field
    collision, not a capability judgement about arbitrary model ids.

    Returns ``""`` when the provider names no usable model of its own; the
    caller then falls back to ``ai_settings.default_summary_model``.
    """
    if provider_key == "openai":
        summary = (provider_cfg.get("summary_model") or "").strip()
        if summary:
            return summary
        legacy = (provider_cfg.get("selected_model") or "").strip()
        return "" if legacy.lower().startswith("whisper") else legacy
    return provider_cfg.get("selected_model") or ""


def _extract_transcription_hotwords(settings_json: Optional[dict]) -> str:
    """Pull the user's ASR hotwords string out of a raw ``settings_json`` value.

    Tolerant of the same dict-or-JSON-string shape the resolver already handles.
    Hotwords are a plain content hint (not a secret / not a model choice), so
    there's no reveal step and no governance gate — they ride through on every
    origin. Returns ``""`` for any missing / malformed input.
    """
    if not settings_json:
        return ""
    settings = settings_json
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except (ValueError, TypeError):
            return ""
    if not isinstance(settings, dict):
        return ""
    ai_settings = settings.get("ai_settings") or {}
    if not isinstance(ai_settings, dict):
        return ""
    hotwords = ai_settings.get("transcription_hotwords", "")
    return hotwords.strip() if isinstance(hotwords, str) else ""


def _with_hotwords(provider_config: Dict[str, Any], hotwords: str) -> Dict[str, Any]:
    """Return a copy of ``provider_config`` carrying a ``hotwords`` key, but only
    when ``hotwords`` is non-empty. Empty hotwords leave the config object
    untouched (identity) so existing origin shapes stay byte-for-byte the same.
    Immutable: never mutates the input dict."""
    if not hotwords or not isinstance(provider_config, dict):
        return provider_config
    return {**provider_config, "hotwords": hotwords}


async def resolve_task_ai_config(
    user_id: Optional[str],
    task_key: str,
    default_slug: str,
) -> ResolvedAIConfig:
    """Resolve a task's assigned agent slug + model + user's BYO provider config.

    Generic form of ``resolve_analyze_provider_config`` (which delegates
    here): reads ``task_assignment[task_key]`` for the agent slug (falling
    back to ``default_slug``), resolves the slug to its ``ai_agents`` row,
    takes its model, derives the provider key from the model prefix, and
    merges the user's BYO entry for that provider.

    Returns a :class:`ResolvedAIConfig` — the resolved slug is returned so the
    caller composes the SAME agent whose model was resolved (see #622/#623:
    prompt agent and model agent must match or the composed model overrides
    the resolved one), and ``origin`` tags which branch produced the config.

    Governance gate
    ---------------
    When an admin has locked this module (``ai_module.<task_key>.user_allowed
    = false`` in system_settings), the user's task_assignment and BYOK are
    IGNORED.  The admin-set config is resolved by the shared
    ``resolve_locked_module_config`` helper, which tries the platform catalog
    FIRST (#857 order) and only then the admin's manual base_url / model /
    api_key.  Locked + no admin api_key (and not a catalog model) →
    fail-closed (RuntimeError) — WhisperService uses
    AIProviderFactory.get_provider directly (no env fallback), so a missing key
    would silently fail; we surface the error early.  ``agent_slug`` is
    ``default_slug`` so the caller composes the module's built-in default agent
    prompt (not a user-assigned one).
    """
    from app.repositories.agent_repository import get_agent_repository
    from app.services.ai.adapters.factory import provider_key_for_model
    from app.services.ai.governance.ai_governance import resolve_locked_module_config

    # ── Governance gate (shared helper — platform-catalog first) ──────────
    locked = await resolve_locked_module_config(task_key)
    if locked is not None:
        return ResolvedAIConfig(
            provider_key=locked.provider_key,
            provider_config=locked.provider_config,
            model=locked.model,
            agent_slug=default_slug,
            origin="governance",
        )

    # Load settings first so we can read the user's assigned agent slug.
    # Reused below for the BYO provider lookup — a single read, not two.
    ai_settings = await get_ai_settings(user_id) if user_id else {}
    assigned_slug = (
        (ai_settings.get("task_assignment") or {}).get(task_key) or default_slug
    ).strip() or default_slug
    resolved_slug = assigned_slug

    # The user may pick a platform model directly (value ``nous:<model>``) instead
    # of an agent — mirrors the ASR task picker. Resolve it to the platform config
    # and compose the module's DEFAULT agent prompt (default_slug). If the model is
    # gone/disabled, degrade to the default agent rather than erroring the task.
    if assigned_slug.startswith("nous:"):
        try:
            nous = await resolve_mediahub_model(assigned_slug[len("nous:") :], task_key)
        except RuntimeError:
            nous = None
        if nous is not None:
            n_provider_key, n_provider_config, n_model = nous
            return ResolvedAIConfig(
                provider_key=n_provider_key,
                provider_config=n_provider_config,
                model=n_model,
                agent_slug=default_slug,
                origin="platform",
            )
        assigned_slug = default_slug
        resolved_slug = default_slug

    agent_repo = get_agent_repository()
    agent = await agent_repo.get_by_slug(assigned_slug)
    if not agent and assigned_slug != default_slug:
        logger.warning(
            f"[AI] {task_key} assigned agent '{assigned_slug}' not found; "
            f"falling back to '{default_slug}'"
        )
        agent = await agent_repo.get_by_slug(default_slug)
        resolved_slug = default_slug

    model = ((agent or {}).get("model") or "").strip()
    if not model:
        logger.warning(
            f"[AI] {task_key} agent '{assigned_slug}' missing or has no "
            "model; caller will use built-in default"
        )
        return ResolvedAIConfig(
            provider_key="",
            provider_config={},
            model="",
            agent_slug=resolved_slug,
            origin=_byok_origin({}),
        )

    # mig 155: the agent row's platform-preset fallback pool, carried into
    # every branch below that resolves FROM this agent's model (spec
    # 2026-08-11-batch-llm-fallback §4). Not populated above (governance /
    # nous:-direct-pick / no-model) because those branches never consult
    # this agent row.
    fallback_models = tuple((agent or {}).get("fallback_models") or [])

    # Shared nous lookup: if the agent's model names a platform Nous model,
    # return the platform config while KEEPING resolved_slug so the caller
    # still composes THIS agent's custom prompt (prompt preserved).
    nous = await resolve_mediahub_model(model, task_key)
    if nous is not None:
        n_provider_key, n_provider_config, n_model = nous
        return ResolvedAIConfig(
            provider_key=n_provider_key,
            provider_config=n_provider_config,
            model=n_model,
            agent_slug=resolved_slug,
            origin="platform",
            fallback_models=fallback_models,
        )

    try:
        provider_key = provider_key_for_model(model)
    except ValueError:
        logger.warning(
            f"[AI] {task_key} agent model '{model}' has unknown provider "
            "prefix; falling back to generic OpenAI-compatible adapter"
        )
        provider_key = ""

    if not user_id or not provider_key:
        return ResolvedAIConfig(
            provider_key=provider_key,
            provider_config={"model": model},
            model=model,
            agent_slug=resolved_slug,
            origin=_byok_origin({"model": model}),
            fallback_models=fallback_models,
        )

    provider_config = dict(get_provider_config(ai_settings, provider_key))
    provider_config["model"] = model
    return ResolvedAIConfig(
        provider_key=provider_key,
        provider_config=provider_config,
        model=model,
        agent_slug=resolved_slug,
        origin=_byok_origin(provider_config),
        fallback_models=fallback_models,
    )


async def resolve_transcription_config(
    user_id: str, settings_json: Optional[dict] = None
) -> ResolvedAIConfig:
    """Resolve the transcription (ASR) provider config for a user run.

    Behaviour-preserving lift of the resolution section that lived inline in
    ``app.workflows.ai_transcription.load_transcribe_inputs`` — moved here so
    the transcription user path resolves through the SAME typed
    :class:`ResolvedAIConfig` as :func:`resolve_task_ai_config` (the
    unification point).

    Unlike the agent-driven tasks, the transcription picker stores a
    ``provider:model`` (or ``nous:<model>``) STRING in
    ``task_assignment.transcription`` — NOT an AI Library agent slug. That
    string is threaded downstream as ``run_whisper``'s ``task_assignment`` arg
    (it derives the Volcengine API resource from it). To keep a single typed
    carrier, this string is returned as :attr:`ResolvedAIConfig.model`:

      - ``"governance"`` → ``model=""`` (the real model rides in
        ``provider_config["model"]`` from the locked config, exactly as the
        workflow returned ``task_assignment=""`` before).
      - ``"platform"`` (a ``nous:<model>`` pick) → ``model="{provider}:{model}"``
        — the normalized descriptor the workflow built.
      - ``"byok"`` / ``"env"`` → ``model`` is the user's raw assignment string.

    ``agent_slug`` is always ``""`` — transcription composes no agent prompt.

    Resolution order (identical to the pre-lift workflow):

    1. Governance gate — ``resolve_locked_module_config("transcription",
       default_provider_key="openai")``. Locked → ``origin="governance"`` and
       the user path is bypassed (user settings are not consulted).
    2. User path — requires ``user_settings``; missing → ``RuntimeError("no
       user_settings for ...")``. Reads ``whisper_provider`` +
       ``task_assignment.transcription``. A ``nous:<model>`` selection resolves
       through the GATED :func:`resolve_mediahub_model` (user-facing pick) and
       fails (``RuntimeError``) on an unknown model — never silently falling
       back. Otherwise the user's BYOK provider entry is used
       (``origin="byok"`` when it carries an api_key, else ``"env"``).

    ``settings_json`` is the user_settings ``settings_json`` value the workflow
    already fetched (dict or JSON string); pass it to avoid a second DB read.
    When ``None`` (and the module isn't governance-locked) the same SQL the
    workflow used is issued here.
    """
    from app.services.ai.governance.ai_governance import resolve_locked_module_config

    # ── Governance gate (shared helper — platform-catalog first) ──────────
    # Check BEFORE consulting user settings so a locked module short-circuits
    # without depending on the user having settings configured.
    locked = await resolve_locked_module_config(
        "transcription", default_provider_key="openai"
    )
    if locked is not None:
        # Hotwords are a content hint, not a model choice, so they ride through
        # even on the admin-locked (governance) path — a user naming people /
        # terms improves recognition regardless of who picked the model. Only
        # injected when non-empty so the locked config shape is otherwise
        # untouched.
        hotwords = _extract_transcription_hotwords(settings_json)
        return ResolvedAIConfig(
            provider_key=locked.provider_key,
            provider_config=_with_hotwords(locked.provider_config, hotwords),
            model="",
            agent_slug="",
            origin="governance",
        )

    # ── User path — user_settings required from here on ───────────────────
    if settings_json is None:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import UserSettings

        async with read_scope() as session:
            settings_json = await session.scalar(
                select(UserSettings.settings_json).where(
                    UserSettings.user_id == user_id
                )
            )
    if not settings_json:
        raise RuntimeError(f"no user_settings for {user_id}")

    settings = settings_json
    if isinstance(settings, str):
        settings = json.loads(settings)
    ai_settings = settings.get("ai_settings", {})
    # This reads raw settings_json directly (not via get_ai_settings) — reveal
    # here so the returned provider_cfg carries the plaintext api_key.
    # user_id is the row owner (the SELECT above is WHERE user_id = :uid, or
    # the caller passed the settings_json it fetched for this same user) —
    # required for the ownership-binding check.
    from app.core.secure_settings import reveal_byok_providers

    providers = reveal_byok_providers(
        ai_settings.get("ai_providers", {}) or {}, user_id=str(user_id)
    )
    whisper_provider = ai_settings.get("whisper_provider", "openai")
    provider_cfg = providers.get(whisper_provider) or {}

    # ASR hotwords (人名 / 术语 hints) — a plain content hint threaded onto the
    # provider_config for every user origin. Non-secret, so read straight from
    # ai_settings (no reveal step). Injected only when non-empty so the empty-
    # config env origin stays exactly ``{}``.
    hotwords = ai_settings.get("transcription_hotwords", "")
    hotwords = hotwords.strip() if isinstance(hotwords, str) else ""

    # task_assignment.transcription carries the model selection like
    # 'volcengine:bigasr' or 'volcengine:seed-asr'. Required for the Volcengine
    # path because the API key may only have one resource granted — picking the
    # wrong one returns 45000030 'resource not granted'.
    task_assignment = ai_settings.get("task_assignment", {}).get("transcription") or ""

    # Nous platform ASR: task_assignment.transcription = 'nous:<model_name>'.
    # Resolve to the platform provider config (GATED — a user-facing pick) and
    # route through the SAME ASR dispatch.
    if task_assignment.startswith("nous:"):
        nous_name = task_assignment.split(":", 1)[1]
        nous = await resolve_mediahub_model(nous_name, "transcription")
        if nous is None:
            raise RuntimeError(
                f"transcription references unknown platform model '{nous_name}'"
            )
        n_provider_key, n_provider_config, n_model = nous
        return ResolvedAIConfig(
            provider_key=n_provider_key,
            provider_config=_with_hotwords(n_provider_config, hotwords),
            model=f"{n_provider_key}:{n_model}",
            agent_slug="",
            origin="platform",
        )

    return ResolvedAIConfig(
        provider_key=whisper_provider,
        provider_config=_with_hotwords(provider_cfg, hotwords),
        model=task_assignment,
        # origin classifies WHOSE credentials serve the run — key the origin off
        # the credential config, NOT the hotwords-augmented copy (an env run
        # with hotwords is still env, not byok).
        agent_slug="",
        origin=_byok_origin(provider_cfg),
    )


async def resolve_summarization_config(
    user_id: str, settings_json: Optional[dict] = None
) -> ResolvedAIConfig:
    """Resolve the summarization provider config for a user run.

    Behaviour-preserving lift of the resolution section that lived inline in
    ``app.workflows.ai_summary.load_summary_inputs`` — moved here so the
    summarization user path resolves through the SAME typed
    :class:`ResolvedAIConfig` as the other resolvers (the unification point).

    Unlike the agent-driven tasks, summarization has NO agent slug and honors
    NO user nous-pick: its user path scans a HARDCODED provider priority
    (``doubao`` → ``qwen`` → ``openai`` → ``deepseek``) for the first provider
    the user has both keyed and enabled, and uses that provider's summary model
    (falling back to ``ai_settings.default_summary_model``).  Which FIELD holds
    that model is per-provider and is decided by
    :func:`_summary_model_from_provider`: ``selected_model`` everywhere except
    ``openai``, whose card writes ``selected_model`` from its *Whisper*
    dropdown and keeps the summary pick in ``summary_model``.
    ``agent_slug`` is therefore always ``""`` — summarization composes no agent
    prompt.

    Resolution order (identical to the pre-lift workflow):

    1. Governance gate — ``resolve_locked_module_config("summarization")``.
       Locked → ``origin="governance"``; the user path is bypassed (user
       settings are not consulted) and provider/config/model come from the
       admin's locked config (``provider_config`` keeps the ``app_id`` shape the
       workflow's dict carried).
    2. User path — requires ``user_settings``; missing → ``RuntimeError("no
       user_settings for ...")``. Scans the hardcoded provider priority for the
       first ``{api_key, enabled}`` provider. When one is found the config
       carries its api_key (``origin="byok"``); when NONE is enabled the loop
       leaves ``provider_key=""`` with an empty (keyless) config
       (``origin="env"``) — the exact fall-through the workflow preserved (no
       raise). ``model`` is the chosen provider's summary model (see
       :func:`_summary_model_from_provider`) else ``default_summary_model``
       else ``""``.

    ``settings_json`` is the user_settings ``settings_json`` value (dict or JSON
    string); pass it to avoid a second DB read. When ``None`` (and the module
    isn't governance-locked) the same SQL the workflow used is issued here.
    """
    from app.services.ai.governance.ai_governance import resolve_locked_module_config

    # ── Governance gate (shared helper — platform-catalog first) ──────────
    # Check BEFORE consulting user settings so a locked module short-circuits
    # without depending on the user having settings configured.
    locked = await resolve_locked_module_config("summarization")
    if locked is not None:
        return ResolvedAIConfig(
            provider_key=locked.provider_key,
            provider_config=locked.provider_config,
            model=locked.model,
            agent_slug="",
            origin="governance",
        )

    # ── User path — user_settings required from here on ───────────────────
    if settings_json is None:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import UserSettings

        async with read_scope() as session:
            settings_json = await session.scalar(
                select(UserSettings.settings_json).where(
                    UserSettings.user_id == user_id
                )
            )
    if not settings_json:
        raise RuntimeError(f"no user_settings for {user_id}")

    settings = settings_json
    if isinstance(settings, str):
        settings = json.loads(settings)
    ai_settings = settings.get("ai_settings", {})
    # This reads raw settings_json directly (not via get_ai_settings) — reveal
    # here so the chosen provider's config carries the plaintext api_key.
    # user_id is the row owner (SELECT WHERE user_id = :uid, or the caller
    # passed this same user's settings_json) — required for the
    # ownership-binding check.
    from app.core.secure_settings import reveal_byok_providers

    providers = reveal_byok_providers(
        ai_settings.get("ai_providers", {}) or {}, user_id=str(user_id)
    )

    chosen_key: Optional[str] = None
    chosen_cfg: Dict[str, Any] = {}
    for key in ("doubao", "qwen", "openai", "deepseek"):
        cfg = providers.get(key)
        if cfg and cfg.get("api_key") and cfg.get("enabled"):
            chosen_key, chosen_cfg = key, cfg
            break

    model = (
        _summary_model_from_provider(chosen_key or "", chosen_cfg)
        or ai_settings.get("default_summary_model")
        or ""
    )
    provider_config: Dict[str, Any] = {
        "api_key": chosen_cfg.get("api_key", ""),
        "base_url": chosen_cfg.get("base_url", ""),
        "app_id": chosen_cfg.get("app_id", ""),
        "model": model,
    }
    return ResolvedAIConfig(
        provider_key=chosen_key or "",
        provider_config=provider_config,
        model=model,
        agent_slug="",
        origin=_byok_origin(provider_config),
    )


async def resolve_chat_config(
    user_id: Any,
    *,
    model: str,
    load_user_config: Callable[[], Awaitable[Dict[str, Any]]],
    agent_slug: str = "",
) -> ResolvedAIConfig:
    """Resolve the chat module's provider config for one agent turn.

    Behaviour-preserving lift of the governance block that lived inline in
    ``ai_library_chat_wiring.build_agent_runner_stack`` — chat now resolves
    through the SAME typed :class:`ResolvedAIConfig` as the task and
    transcription resolvers (the unification point).

    Chat differs from every other governed module in two ways that shape this
    function's contract:

    1. **Governance is a pure toggle — there is NO admin model/key path.**
       The ``chat`` module only carries ``user_allowed``; an admin cannot pin a
       model or key for it.  So this resolver consults ``get_module_governance``
       DIRECTLY and must NOT route through ``resolve_locked_module_config`` (that
       shared gate fails closed when a locked module has no admin api_key — which
       is always true for chat).  When locked, the user's BYOK lookup is skipped
       and ``get_adapter_for_user`` later receives an empty dict, falling back to
       platform env credentials.  The DECISION was governance, so
       ``origin="governance"`` even though the ultimate credentials are env.

    2. **The agent owns the model.**  Chat's model is the assigned agent's
       ``model`` field, resolved by the caller (``primary_model``) and threaded
       straight into ``get_adapter_for_user`` / ``LLMFallbackChain`` — this
       resolver does NOT select it.  ``ResolvedAIConfig.model`` is therefore left
       ``""``; the ``model`` argument here is used only to derive ``provider_key``
       (the credential set that will serve the turn) and to classify ``origin``.

    ``provider_config`` is the user's FULL ``ai_providers`` dict (NOT a single
    provider's narrowed config like the task resolvers return) because
    ``get_adapter_for_user`` does its own per-model narrowing.  It is ``{}`` when
    locked.  The dict is loaded via the injected ``load_user_config`` awaitable
    ONLY on the allowed path — locking must skip the load entirely (a user BYOK
    read has cost, and the lock means "ignore user config").  Injecting the
    loader keeps this helper free of a dependency on the chat-wiring module and
    preserves the exact seam the governance tests patch.

    origin mapping:
      - locked                                          → ``"governance"``
      - allowed + model's provider has a BYOK api_key   → ``"byok"``
      - allowed + no BYOK key for the model's provider  → ``"env"``
    """
    from app.services.ai.adapters.factory import provider_key_for_model
    from app.services.ai.governance.ai_governance import (
        get_module_governance,
        get_platform_ai_providers,
    )

    try:
        provider_key = provider_key_for_model(model)
    except ValueError:
        provider_key = ""

    # Platform credentials live in the DATABASE (system_settings
    # ``platform.ai_providers``) like every other AI key in this system;
    # env vars are only the last-resort bootstrap inside
    # ``get_adapter_for_user``. Same dict shape as user BYOK, so it merges
    # beneath it: user entry wins per provider.
    platform_providers = await get_platform_ai_providers()

    governance = await get_module_governance("chat")
    if not governance.allowed:
        logger.info(
            "[governance] chat locked by admin for user {}; skipping user BYO "
            "keys — using platform provider keys",
            user_id,
        )
        return ResolvedAIConfig(
            provider_key=provider_key,
            provider_config=platform_providers,
            model="",
            agent_slug=agent_slug,
            origin="governance",
        )

    providers = await load_user_config()
    # Preserve object identity when there are no platform creds — the
    # pre-existing contract is that the user's ai_providers dict flows
    # through untouched; only a real merge justifies a new dict.
    merged = (
        {**platform_providers, **(providers or {})}
        if platform_providers
        else (providers or {})
    )
    raw_entry = merged.get(provider_key) if provider_key else None
    entry = raw_entry if isinstance(raw_entry, dict) else {}
    # origin classifies whose credentials serve the turn: "byok" only when
    # the USER's own entry carries the key; a platform-DB entry is
    # "platform"; neither → "env" (adapter factory bootstrap fallback).
    raw_user_entry = (providers or {}).get(provider_key) if provider_key else None
    user_entry = raw_user_entry if isinstance(raw_user_entry, dict) else {}
    if (user_entry.get("api_key") or "") and str(user_entry.get("api_key")).strip():
        origin = "byok"
    elif (entry.get("api_key") or "") and str(entry.get("api_key")).strip():
        origin = "platform"
    else:
        origin = "env"
    return ResolvedAIConfig(
        provider_key=provider_key,
        provider_config=merged,
        model="",
        agent_slug=agent_slug,
        origin=origin,
    )


async def resolve_task_provider_config(
    user_id: Optional[str],
    task_key: str,
    default_slug: str,
) -> Tuple[str, Dict[str, Any], str, str]:
    """Backwards-compatible tuple shim over :func:`resolve_task_ai_config`.

    Returns ``(provider_key, provider_config, model, agent_slug)`` — byte-for-
    byte the legacy return shape, so all existing call sites stay untouched.
    New code that needs the resolution ``origin`` should call
    ``resolve_task_ai_config`` directly.
    """
    cfg = await resolve_task_ai_config(user_id, task_key, default_slug)
    return cfg.provider_key, cfg.provider_config, cfg.model, cfg.agent_slug


async def resolve_analyze_provider_config(
    user_id: Optional[str],
) -> Tuple[str, Dict[str, Any], str, str]:
    """Resolve the visual-analysis agent slug + model + user's BYO provider config.

    Returns ``(provider_key, provider_config, model, agent_slug)``. Honors the
    user's ``task_assignment.visual_analysis`` setting — as of migration 142
    (Phase 2 PR 2.8b) that key stores an AI Library agent slug (e.g. 'analyze',
    or a custom 'test-analyze'). We resolve that slug to its ``ai_agents`` row,
    take its ``model``, derive the provider prefix, then pull the user's BYO
    entry for that provider out of ``ai_settings.ai_providers``.

    ``agent_slug`` (the resolved slug, after any fallback) is returned so the
    caller composes the SAME agent's prompt as the one whose model we resolved.
    VisualAnalysisService composes the agent's IDENTITY/SOUL/AGENT to build its
    system prompt, and ``composed.model`` (the composed agent's model) is what
    actually drives the adapter — so the prompt agent and the model agent MUST
    be the same one, else the composed model overrides the resolved one.

    Falls back to the built-in ``analyze`` agent when the assignment is unset
    or the assigned slug doesn't resolve. When ``user_id`` is None or the agent
    row is missing, returns empty config and lets the service route through the
    factory's default.

    Bug history:
      - #622 fixed the slug read here, but the run still used qwen-max because
        VisualAnalysisService hardcoded the prompt agent to 'analyze', and the
        composed (qwen-max) model overrode the resolved doubao one. Returning
        ``agent_slug`` lets the caller compose the right agent end-to-end.
      - Originally hardcoded ``get_by_slug("analyze")``, so a user who assigned a
        different agent (and only configured that agent's provider as BYO) always
        got 'analyze'\\'s qwen-max with no matching config → empty config →
        "All connection attempts failed".
    """
    return await resolve_task_provider_config(
        user_id, "visual_analysis", DEFAULT_ANALYZE_AGENT_SLUG
    )


async def resolve_translate_provider_config(
    user_id: Optional[str],
) -> Tuple[str, Dict[str, Any], str, str]:
    """Resolve the translation agent slug + model + user's BYO provider config.

    Honors ``task_assignment.translation`` (an AI Library agent slug),
    defaulting to the built-in ``translate`` agent. Same return shape as
    the other resolvers: ``(provider_key, provider_config, model, slug)``.
    """
    return await resolve_task_provider_config(
        user_id, "translation", DEFAULT_TRANSLATE_AGENT_SLUG
    )


async def resolve_script_provider_config(
    user_id: Optional[str],
) -> Tuple[str, Dict[str, Any], str, str]:
    """Resolve the script-generation agent slug + model + user's BYO provider
    config. Honors ``task_assignment.script_generation`` (an AI Library agent
    slug), defaulting to the built-in ``script_ai`` agent. Same return shape as
    the other resolvers: ``(provider_key, provider_config, model, slug)``."""
    return await resolve_task_provider_config(
        user_id, "script_generation", DEFAULT_SCRIPT_AGENT_SLUG
    )


async def resolve_caption_provider_config(
    user_id: Optional[str],
) -> Tuple[str, Dict[str, Any], str, str]:
    """Resolve the image-caption agent slug + model + user's BYO provider config.

    Honors ``task_assignment.caption`` (an AI Library agent slug),
    defaulting to the built-in ``caption`` agent. The assigned agent's
    model must be a vision/multimodal one — the caption workflow surfaces
    a clear error when the provider call fails.
    """
    return await resolve_task_provider_config(
        user_id, "caption", DEFAULT_CAPTION_AGENT_SLUG
    )


async def resolve_classify_provider_config(
    user_id: Optional[str],
) -> Tuple[str, Dict[str, Any], str, str]:
    """Resolve the asset-classification agent slug + model + BYO config.

    Honors ``task_assignment.classification`` (an AI Library agent slug),
    defaulting to the built-in ``classify`` agent (vision required).
    """
    return await resolve_task_provider_config(
        user_id, "classification", DEFAULT_CLASSIFY_AGENT_SLUG
    )
