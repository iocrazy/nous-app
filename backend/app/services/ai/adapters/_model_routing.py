"""Route-authoritative wire-model resolution (harness audit #8, fix C).

The model name an adapter sends on the wire MUST equal the model the adapter
resolved its endpoint + API key for — its ``default_model``. The factory
(``get_adapter_for_user`` / ``get_adapter``) always picks the provider URL +
key FROM the model argument and bakes that same model in as ``default_model``,
so ``default_model`` is the single source of truth for *where* the request
goes.

When a caller passes a ``composed.model`` that disagrees with the resolved
``default_model`` (both non-empty), sending ``composed.model`` would hit
provider-X's URL + key carrying provider-Y's model name — a silent misroute.
That is exactly the ark-key visual-analysis incident class: a provider
resolved for one model, a prompt composed for another. We refuse to misroute:
log loudly, count it, and self-heal to the route-authoritative
``default_model``.

``composed.model`` is honored only when ``default_model`` is empty — the case
of an adapter built generically without a baked model, where ``composed.model``
legitimately carries the routing decision.
"""

from __future__ import annotations

from loguru import logger

from app.agent_framework._metrics_helper import inc_metric


def resolve_wire_model(composed_model: str, default_model: str) -> str:
    """Return the model name to send on the wire.

    Aligned / empty cases preserve the historical ``composed_model or
    default_model`` precedence (zero behavior change). A genuine both-set
    mismatch is treated as an upstream routing bug: logged, counted, and
    healed to ``default_model`` (the model the endpoint + key were chosen for).
    """
    if composed_model and default_model and composed_model != default_model:
        logger.error(
            "[adapter] wire-model mismatch: composed.model={!r} != resolved "
            "default_model={!r}; self-healing to resolved model "
            "(route-authoritative). An upstream caller built an adapter for one "
            "model but composed another — see harness audit #8.",
            composed_model,
            default_model,
        )
        inc_metric("adapter_wire_model_mismatch")
        return default_model
    return composed_model or default_model


__all__ = ["resolve_wire_model"]
