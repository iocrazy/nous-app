"""``context_measured`` is a LOCAL measurement fed by the recorder on every
compaction check (green/yellow tiers write no transcript event, yet the
pressure gauge should move each turn). Never inserted as an event.

``view.context`` is ``{used_pct, window, window_source}`` for every writer
(this fold and the compaction bracket). ``window_source`` is where the
denominator came from — catalog / builtin / fallback — and is ``None`` for a
payload that predates it or carries anything else."""

from typing import Any, Optional

from app.services.ai.runner.run_projection import register

WINDOW_SOURCES = frozenset({"catalog", "builtin", "fallback"})


def window_source_of(value: Any) -> Optional[str]:
    """The source when it is one we know, else ``None`` — never pass through."""
    return value if isinstance(value, str) and value in WINDOW_SOURCES else None


def context_view(used: int, window: int, source: Any) -> dict:
    return {
        "used_pct": round(used * 100 / window),
        "window": window,
        "window_source": window_source_of(source),
    }


@register("context_measured")
def fold_context(views, payload):
    used, window = payload.get("used"), payload.get("window")
    if not isinstance(used, int) or not isinstance(window, int) or window <= 0:
        return None
    views["view"]["context"] = context_view(used, window, payload.get("window_source"))
    return views
