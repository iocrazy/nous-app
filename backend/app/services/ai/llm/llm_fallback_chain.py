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

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.llm.llm_retry_middleware import (
    CancelCheck,
    LLMCallError,
    LLMRetryExhausted,
    LLMRetryMiddleware,
    RunCancelled,
)

logger = logging.getLogger(__name__)


class AllModelsFailed(Exception):
    """Primary + every fallback exhausted retries. Run must abort."""


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

    # Sprint 3: optional health registry. None = legacy behavior (try
    # every model in order, no skip-known-bad).
    health_registry: Optional[Any] = None

    _switch_log: list[_SwitchEvent] = field(default_factory=list, init=False)

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

        for idx, model in enumerate(models):
            # Sprint 3: skip recently-failed models. The registry has
            # already discovered their cooldown via report_status from
            # a prior call's failure — no point burning retries on them.
            if (
                self.health_registry is not None
                and not self.health_registry.is_available(model)
            ):
                logger.info(
                    "[Fallback] %s skipped (cooled down by health registry)",
                    model,
                )
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
                    "[Fallback] adapter init failed for model=%s: %s",
                    model,
                    type(exc).__name__,
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

            mw = LLMRetryMiddleware(
                adapter,
                cancel_check=self.cancel_check,
                max_retries=self.max_retries_per_model,
                base_delay_s=self.base_delay_s,
                max_delay_s=self.max_delay_s,
            )

            try:
                response = await mw.call(composed, messages)
            except RunCancelled:
                # Cancel terminates the whole chain — no fallback attempts.
                raise
            except LLMCallError:
                # Auth / bad-request failures don't recover via fallback.
                raise
            except LLMRetryExhausted as exc:
                logger.warning(
                    "[Fallback] %s exhausted retries; trying next model", model
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

        # Every model exhausted. Surface the last exception's chain for
        # observability.
        raise AllModelsFailed(
            f"primary + {len(self.fallback_models)} fallback(s) exhausted"
        ) from last_exc

    def _build_adapter(self, model: str) -> Any:
        return self.adapter_factory(model)


__all__ = [
    "AdapterFactory",
    "AllModelsFailed",
    "LLMFallbackChain",
]
