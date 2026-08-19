"""FALLBACK chain — walks ``ai_agents.fallback_models`` after primary exhausts.

Composition with retry middleware (locked in plan-eng-review 2026-04-25):

    primary_adapter (Qwen-Max)
        wrapped by LLMRetryMiddleware → retries N times → raises LLMRetryExhausted
            caught by LLMFallbackChain
                → swap to fallback_models[0] (e.g. Qwen-Plus)
                → wrap fresh adapter in LLMRetryMiddleware → retries N times
                    → succeed → return response (with .model snapshot updated)
                    → exhaust → swap to fallback_models[1] → ...
                    → all exhausted → raise AllModelsFailed

Cost snapshot (recorded by RunRecorder) reflects the model that ACTUALLY
served the response, not the configured primary. The chain emits a
``switch_log`` list in the response under ``_fallback_meta`` so the
chat service can persist into ``agent_runs.metadata_json``.

This module deliberately knows about model NAMES only — it asks the
``adapter_factory`` callable to build a fresh adapter per swap. The
factory closure is wired by the chat service (``get_adapter(model)``).

LLMCallError (non-retryable, e.g. 401) is NOT caught here — auth
failures don't get better with a different model. Surface them up.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.llm.llm_retry_middleware import (
    CancelCheck,
    LLMCallError,
    LLMRetryExhausted,
    LLMRetryMiddleware,
    RunCancelled,
    describe_llm_error,
)


class AllModelsFailed(Exception):
    """Primary + every fallback exhausted retries. Run must abort.

    The message is deliberately SELF-CONTAINED — it names every model tried
    and why each one gave up. It used to read only
    ``"primary + 0 fallback(s) exhausted"`` and lean on ``__cause__`` for the
    reason, which loses the reason completely on the path that matters most:
    DBOS pickles an exception's ``args`` and drops ``__cause__``, so the
    2026-08-19 ai_summary failures reached ``task_tracking`` carrying a model
    count and nothing else, while the actual cause (a Volcengine
    account-level ``SetLimitExceeded`` cap on doubao-seed-2-0-pro) survived
    only in a container's stderr.

    ``attempts`` keeps the same information structured for in-process callers
    (classification, tests, future UI) — but never assume it survives a
    workflow boundary; only the message string does.
    """

    def __init__(self, message: str, *, attempts: Optional[list[dict]] = None):
        super().__init__(message)
        self.attempts: list[dict] = list(attempts or [])


# Adapter factory contract: given a model id, return a fresh adapter
# instance. Callers (chat service) provide a closure over their settings
# / user BYO config so this module stays decoupled from secrets handling.
AdapterFactory = Callable[[str], Any]


@dataclass(frozen=True)
class _SwitchEvent:
    """One row in the fallback switch log."""

    from_model: str
    to_model: str
    reason: str  # 'retries_exhausted' / 'adapter_init_failed'


@dataclass
class LLMFallbackChain:
    """Wraps primary + fallback chain. Returns first model to succeed.

    Sprint 3: optional ``health_registry`` consults a per-process
    ``ModelHealthRegistry`` to skip recently-failed models. A model
    that just hit 429 won't be retried for 60s — fallback fires
    immediately on the next call instead of burning the retry budget
    on the known-bad primary again. Result: 5-30s per call shaved off
    when primary is in a temporary outage.
    """

    primary_model: str
    fallback_models: list[str]
    adapter_factory: AdapterFactory
    cancel_check: Optional[CancelCheck] = None

    # Per-attempt retry settings — passed through to LLMRetryMiddleware.
    max_retries_per_model: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0

    # AI-007: hard ceiling on total wall-time across the WHOLE chain
    # (every model's retries + backoffs combined). None = no ceiling. When
    # set, the chain stops trying further models once the deadline passes and
    # shares the remaining budget with each model's inner retry middleware, so
    # the worst case is bounded instead of (models × max_retries × backoff).
    total_deadline_seconds: Optional[float] = None

    # Sprint 3: optional health registry. None = legacy behavior (try
    # every model in order, no skip-known-bad).
    health_registry: Optional[Any] = None

    _switch_log: list[_SwitchEvent] = field(default_factory=list, init=False)
    # Test seam: override the monotonic clock for deterministic deadline tests.
    # default_factory to keep the instance-attribute pattern — see the
    # descriptor-binding note on LLMRetryMiddleware.sleep (a plain-function
    # dataclass default binds as a method; builtins like time.monotonic
    # happen not to, but don't rely on that).
    monotonic: Callable[[], float] = field(
        default_factory=lambda: time.monotonic, init=False
    )

    @property
    def switch_log(self) -> list[_SwitchEvent]:
        return list(self._switch_log)

    async def call(
        self, composed: ComposedSystemPrompt, messages: list[dict]
    ) -> dict[str, Any]:
        """Try primary, then each fallback. Returns first successful response.

        On success: response dict has ``_fallback_meta`` injected with the
        actual model used + switch log (caller persists to
        ``agent_runs.metadata_json``). Caller also reads ``_actual_model``
        to update ``agent_runs.model`` snapshot.
        """
        models = [self.primary_model, *self.fallback_models]
        last_exc: Optional[BaseException] = None
        # One row per model the chain touched, in order — the raw material for
        # both the AllModelsFailed message and post-mortem log lines. Before
        # this existed, a fully-exhausted chain left NOTHING queryable behind
        # (application_logs had zero rows for this module: it logged through
        # stdlib logging, which app/core/utils.py bridges into loguru only for
        # an allowlist of third-party logger names).
        attempts: list[dict] = []
        start = self.monotonic()

        for idx, model in enumerate(models):
            # AI-007: stop walking the chain once the global deadline passes.
            if idx > 0 and self._deadline_remaining(start) <= 0:
                logger.warning(
                    f"[Fallback] global deadline "
                    f"({self.total_deadline_seconds}s) reached; not trying "
                    f"{model} or later models"
                )
                attempts.append({"model": model, "outcome": "deadline_reached"})
                break
            # Sprint 3: skip recently-failed models. The registry has
            # already discovered their cooldown via report_status from
            # a prior call's failure — no point burning retries on them.
            if (
                self.health_registry is not None
                and not self.health_registry.is_available(model)
            ):
                logger.warning(
                    f"[Fallback] {model} skipped (cooled down by health registry)"
                )
                attempts.append({"model": model, "outcome": "cooled_down"})
                if idx > 0:
                    self._switch_log.append(
                        _SwitchEvent(
                            from_model=models[idx - 1],
                            to_model=model,
                            reason="model_cooled_down",
                        )
                    )
                continue

            try:
                adapter = self._build_adapter(model)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"[Fallback] adapter init failed for model={model}: "
                    f"{describe_llm_error(exc)}"
                )
                attempts.append(
                    {
                        "model": model,
                        "outcome": "adapter_init_failed",
                        "error": describe_llm_error(exc),
                    }
                )
                if idx > 0:
                    self._switch_log.append(
                        _SwitchEvent(
                            from_model=models[idx - 1],
                            to_model=model,
                            reason="adapter_init_failed",
                        )
                    )
                last_exc = exc
                continue

            # AI-007: share the remaining chain-wide budget with this model's
            # retry loop so a single slow model can't overrun the whole ceiling.
            remaining = self._deadline_remaining(start)
            mw = LLMRetryMiddleware(
                adapter,
                cancel_check=self.cancel_check,
                max_retries=self.max_retries_per_model,
                base_delay_s=self.base_delay_s,
                max_delay_s=self.max_delay_s,
                total_deadline_seconds=(
                    None if remaining == float("inf") else max(0.0, remaining)
                ),
            )

            # Audit #8 (fix A): the wire model MUST match the adapter we just
            # built. ``adapter_factory(model)`` resolved THIS model's provider
            # endpoint + key and baked ``default_model=model``; ``composed.model``
            # still holds the PRIMARY name on every fallback attempt. Realign it
            # per attempt or fallback misroutes (cross-provider: right key, wrong
            # model name → 400) or silently re-calls the failing primary
            # (same-provider). Copy only when it actually differs so the common
            # primary path keeps object identity and avoids needless churn.
            #
            # COUPLING: this enforces "wire model == resolved model". If
            # ``model_override`` is ever wired through prompt_composer, then
            # ``primary_model`` in ai_library_chat_wiring.py MUST derive from it
            # too — otherwise this realignment clobbers the override on the
            # primary attempt.
            composed_attempt = (
                composed
                if composed.model == model
                else composed.model_copy(update={"model": model})
            )

            try:
                response = await mw.call(composed_attempt, messages)
            except RunCancelled:
                # Cancel terminates the whole chain — no fallback attempts.
                raise
            except LLMCallError:
                # Auth / bad-request failures don't recover via fallback.
                raise
            except LLMRetryExhausted as exc:
                # ``__cause__`` is the provider exception the middleware
                # gave up on; it holds the status + body. ``exc`` itself only
                # counts attempts. (Deliberately NOT named ``remaining`` —
                # that name is the deadline budget a few lines up.)
                reason = describe_llm_error(exc.__cause__ or exc)
                models_left = len(models) - idx - 1
                logger.warning(
                    f"[Fallback] {model} exhausted retries ({reason}); "
                    + (
                        f"trying next model ({models_left} left)"
                        if models_left
                        else "no models left"
                    )
                )
                attempts.append(
                    {
                        "model": model,
                        "outcome": "retries_exhausted",
                        "error": reason,
                    }
                )
                last_exc = exc
                # Sprint 3: report to health registry so subsequent
                # calls skip this model until cooldown expires. Use 429
                # as the proxy status for "exhausted retries" — same
                # cooldown duration as a single 429.
                if self.health_registry is not None:
                    self.health_registry.report_status(model, 429)
                if idx < len(models) - 1:
                    self._switch_log.append(
                        _SwitchEvent(
                            from_model=model,
                            to_model=models[idx + 1],
                            reason="retries_exhausted",
                        )
                    )
                continue

            # Sprint 3: success — clear any stale cooldown for this
            # model in the registry (in case its TTL expired between
            # is_available check and now).
            if self.health_registry is not None:
                self.health_registry.mark_recovered(model)

            # Success — annotate and return.
            response["_fallback_meta"] = {
                "actual_model": model,
                "primary_model": self.primary_model,
                "fallback_used": idx > 0,
                "switch_log": [
                    {"from": e.from_model, "to": e.to_model, "reason": e.reason}
                    for e in self._switch_log
                ],
            }
            response["_actual_model"] = model
            return response

        # Every model exhausted. The message must stand alone — see the
        # AllModelsFailed docstring for why ``from last_exc`` is not enough.
        raise AllModelsFailed(
            self._exhausted_message(attempts), attempts=attempts
        ) from last_exc

    def _exhausted_message(self, attempts: list[dict]) -> str:
        """Human-readable summary of an exhausted chain.

        Shape: ``"all N model(s) failed: <model> (<reason>); <model> (…)"`` —
        the model names first (they answer "which key/quota do I go fix?"),
        then each model's own reason. Reasons are already truncated per
        attempt by ``describe_llm_error``; the whole string is clipped again
        so a long chain can't blow past ``task_tracking.error_msg``'s 500
        chars and push the model names out of view.
        """
        total = 1 + len(self.fallback_models)
        if not attempts:
            # Only reachable if every model was skipped before being tried.
            return f"all {total} model(s) failed (none was attempted)"
        parts = [
            f"{a['model']} ({a.get('error') or a.get('outcome')})" for a in attempts
        ]
        return f"all {total} model(s) failed: " + "; ".join(parts)[:400]

    def _deadline_remaining(self, start: float) -> float:
        """Seconds left before the chain-wide deadline; +inf when unset."""
        if self.total_deadline_seconds is None:
            return float("inf")
        return self.total_deadline_seconds - (self.monotonic() - start)

    def _build_adapter(self, model: str) -> Any:
        return self.adapter_factory(model)


__all__ = [
    "AdapterFactory",
    "AllModelsFailed",
    "LLMFallbackChain",
]
