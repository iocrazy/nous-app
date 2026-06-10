"""Tasklet base — single-turn cheap-model micro-call abstraction.

Design contract (see ``__init__.py`` for motivation):

- One LLM call, one system_prompt, one user input → one parsed result.
- Default model is ``qwen-turbo`` (cheap). Subclasses override via field.
- If ``output_schema`` is set, response is parsed as JSON-strict and
  validated against the schema. Otherwise raw text is returned.
- Failures (LLM exception, malformed JSON, schema mismatch) return a
  :class:`TaskletResult` with ``ok=False`` and ``error`` set. **Never raise**
  unless caller explicitly opts in with ``run_or_raise``.
- ``cache_fingerprint`` is derived from ``slug`` so the prompt-cache
  layer (already in :mod:`app.services.ai.prompts.prompt_composer`) can
  share cache across runs of the same tasklet.

This module is intentionally synchronous in shape (run is async because
the adapter is async, but the Tasklet itself is a frozen dataclass with
no internal state).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# Sentinel agent_id for tasklets — distinguishes from real agent UUIDs in
# RunRecorder / agent_run_events queries.
_TASKLET_AGENT_ID = UUID(int=0)


@dataclass(frozen=True)
class TaskletResult:
    """Return shape of :meth:`Tasklet.run`.

    Always returned; callers don't have to wrap in try/except. Check ``ok``
    before reading ``value``.
    """

    ok: bool
    value: Any = None  # str if no schema; dict if schema; None if !ok
    error: Optional[str] = None  # short reason when ok=False
    raw: Optional[str] = None  # raw model text for forensics (truncated)


@dataclass(frozen=True)
class Tasklet:
    """Single-turn cheap-model tasklet definition.

    Subclass-friendly via dataclass field overrides:

        @dataclass(frozen=True)
        class IntentClassifier(Tasklet):
            slug: str = "intent_classifier"
            system_prompt: str = "Classify user intent..."
            output_schema: Optional[Dict[str, Any]] = field(
                default_factory=lambda: {...}
            )

    Or use the :func:`make_tasklet` factory when subclassing is overkill.
    """

    slug: str
    system_prompt: str
    output_schema: Optional[Dict[str, Any]] = None
    model: str = "qwen-turbo"
    temperature: float = 0.0
    max_tokens: int = 512
    cache_fingerprint_suffix: str = "v1"  # bump when prompt changes

    # Optional: per-tasklet user-input formatter. Default = identity.
    # Subclasses override to e.g. join a list of messages.
    def format_input(self, user_input: Any) -> str:
        """Render the caller's user_input into a string for the LLM.

        Default impl passes through ``str(user_input)``. Override for
        complex inputs (e.g. lists of messages).
        """
        if isinstance(user_input, str):
            return user_input
        return str(user_input)

    @property
    def cache_fingerprint(self) -> str:
        return f"tasklet_{self.slug}_{self.cache_fingerprint_suffix}"

    async def run(self, user_input: Any, *, settings: Any) -> TaskletResult:
        """Execute the tasklet against the configured cheap model.

        Catches LLM exceptions and malformed JSON; never raises (use
        :meth:`run_or_raise` if you want exceptions).

        ``settings`` is mediahub's global settings object; passed through
        to the adapter factory.
        """
        # Defer imports so the module is cheap to import in test paths
        # that don't touch the LLM stack.
        from app.schemas.ai_library import ComposedSystemPrompt
        from app.services.ai.adapters.factory import get_adapter

        formatted = self.format_input(user_input)

        composed = ComposedSystemPrompt(
            agent_id=_TASKLET_AGENT_ID,
            agent_slug=f"tasklet.{self.slug}",
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            system_message=self.system_prompt,
            tools=[],
            skill_manifest=[],
            cache_fingerprint=self.cache_fingerprint,
        )

        try:
            adapter = get_adapter(self.model, settings)
            resp = await adapter.call(
                composed,
                [{"role": "user", "content": formatted}],
            )
        except Exception as exc:  # noqa: BLE001 — tasklets must not raise
            logger.exception("[tasklet:%s] LLM call failed", self.slug)
            return TaskletResult(ok=False, error=f"llm_call_failed: {exc}")

        try:
            text = resp["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning(
                "[tasklet:%s] unexpected response shape: %s", self.slug, exc
            )
            return TaskletResult(
                ok=False,
                error="bad_response_shape",
                raw=str(resp)[:500] if resp else None,
            )

        if self.output_schema is None:
            # Raw text mode — caller does its own parsing.
            return TaskletResult(ok=True, value=text.strip(), raw=text[:500])

        parsed = parse_json_response(text)
        if parsed is None:
            return TaskletResult(
                ok=False,
                error="malformed_json",
                raw=text[:500],
            )

        if not _matches_schema(parsed, self.output_schema):
            return TaskletResult(
                ok=False,
                error="schema_mismatch",
                value=parsed,  # caller can still inspect partial parse
                raw=text[:500],
            )

        return TaskletResult(ok=True, value=parsed, raw=text[:500])

    async def run_or_raise(self, user_input: Any, *, settings: Any) -> Any:
        """Like :meth:`run` but raises ``TaskletError`` on failure.

        Use when downstream code can't sensibly handle ``None`` and you'd
        rather propagate the error up.
        """
        result = await self.run(user_input, settings=settings)
        if not result.ok:
            raise TaskletError(
                f"tasklet {self.slug} failed: {result.error}",
                tasklet_slug=self.slug,
                raw=result.raw,
            )
        return result.value


class TaskletError(RuntimeError):
    """Raised by :meth:`Tasklet.run_or_raise` on tasklet failure."""

    def __init__(
        self,
        message: str,
        *,
        tasklet_slug: str,
        raw: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.tasklet_slug = tasklet_slug
        self.raw = raw


# ============================================================================
# Helpers — JSON parsing + schema validation
# ============================================================================


def parse_json_response(text: str) -> Optional[Any]:
    """Robust JSON-from-model parser.

    Handles:
    - Plain JSON
    - JSON wrapped in ``` markdown fences (with or without ``json`` tag)
    - Leading/trailing whitespace

    Returns the parsed value, or ``None`` on any parse failure.
    """
    if not text:
        return None
    stripped = text.strip()

    # Strip markdown code fences if present.
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
        elif stripped.startswith("JSON"):
            stripped = stripped[4:]
        stripped = stripped.strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        logger.debug("[tasklet] non-JSON response; got %r", text[:200])
        return None


def _matches_schema(payload: Any, schema: Dict[str, Any]) -> bool:
    """Minimal schema check — verifies ``type``, ``required``, and ``enum``.

    NOT full JSON Schema. For richer validation, callers can plug in jsonschema
    library later. The minimum we want here is "doesn't fail silently if the
    LLM returned a string instead of an object".
    """
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(payload, dict):
            return False
        required = schema.get("required") or []
        for key in required:
            if key not in payload:
                return False
        # Validate enum on each required field if specified
        properties = schema.get("properties") or {}
        for key, spec in properties.items():
            if key not in payload:
                continue  # only required fields enforced above
            if "enum" in spec and payload[key] not in spec["enum"]:
                return False
        return True
    elif expected_type == "array":
        return isinstance(payload, list)
    elif expected_type == "string":
        return isinstance(payload, str)
    elif expected_type == "integer":
        return isinstance(payload, int) and not isinstance(payload, bool)
    elif expected_type == "number":
        return isinstance(payload, (int, float)) and not isinstance(payload, bool)
    elif expected_type == "boolean":
        return isinstance(payload, bool)
    # Unknown type — be lenient.
    return True


__all__ = [
    "Tasklet",
    "TaskletError",
    "TaskletResult",
    "parse_json_response",
]
