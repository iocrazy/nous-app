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

    Returns an :class:`AIAdapter`. Platform credentials are injected under
    BOTH the catalog's ``actual_provider`` and the factory's own prefix-derived
    key, so a catalog row whose ``actual_provider`` label drifts from the
    model-prefix convention still resolves.
    """
    from app.services.ai.adapters.factory import (
        get_adapter_for_user,
        provider_key_for_model,
    )

    hit = await resolve_mediahub_model(model, module)
    if hit:
        actual_provider, cfg, actual_model = hit
        creds = {"api_key": cfg["api_key"], "base_url": cfg["base_url"]}
        injected: Dict[str, Any] = {actual_provider: creds}
        try:
            injected[provider_key_for_model(actual_model)] = creds
        except ValueError:
            pass  # unknown prefix — factory will raise the same error below
        return get_adapter_for_user(actual_model, injected, None)

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
    fail-closed (RuntimeError) — WhisperService and LLMAnalysisService use
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
        )

    provider_config = dict(get_provider_config(ai_settings, provider_key))
    provider_config["model"] = model
    return ResolvedAIConfig(
        provider_key=provider_key,
        provider_config=provider_config,
        model=model,
        agent_slug=resolved_slug,
        origin=_byok_origin(provider_config),
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
        return ResolvedAIConfig(
            provider_key=locked.provider_key,
            provider_config=locked.provider_config,
            model="",
            agent_slug="",
            origin="governance",
        )

    # ── User path — user_settings required from here on ───────────────────
    if settings_json is None:
        from app.db import engine as db_engine

        settings_row = await db_engine.fetch_one(
            "SELECT settings_json FROM public.user_settings WHERE user_id = :uid",
            {"uid": user_id},
        )
        if not settings_row:
            raise RuntimeError(f"no user_settings for {user_id}")
        settings_json = settings_row["settings_json"]
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
            provider_config=n_provider_config,
            model=f"{n_provider_key}:{n_model}",
            agent_slug="",
            origin="platform",
        )

    return ResolvedAIConfig(
        provider_key=whisper_provider,
        provider_config=provider_cfg,
        model=task_assignment,
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
    the user has both keyed and enabled, and uses that provider's
    ``selected_model`` (falling back to ``ai_settings.default_summary_model``).
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
       raise). ``model`` is the chosen provider's ``selected_model`` else
       ``default_summary_model`` else ``""``.

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
        from app.db import engine as db_engine

        settings_row = await db_engine.fetch_one(
            "SELECT settings_json FROM public.user_settings WHERE user_id = :uid",
            {"uid": user_id},
        )
        if not settings_row:
            raise RuntimeError(f"no user_settings for {user_id}")
        settings_json = settings_row["settings_json"]
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
        chosen_cfg.get("selected_model")
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
