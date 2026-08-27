"""codex-local chat adapter — the LLM call runs on the USER's machine.

Not a streaming adapter on purpose: the daemon answers once, when
``codex exec`` is done. AgentRunner sees no ``stream`` attribute and takes
its buffered path (``agent_runner.py`` ``_stream_turn_inner``).

Hard limits (spec §1): no tool calling, no system-prompt flag, no messages
array. Tools present ⇒ typed rejection BEFORE dispatch, never a silent
text-only degrade.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.codex.daemon_dispatch import DaemonOfflineError, dispatch_to_daemon
from app.services.codex.errors import CodexLocalError, from_daemon_error
from app.services.codex.flatten import flatten_for_codex
from app.services.codex.personal_scope import resolve_personal_scope_id

DEFAULT_TEXT_TIMEOUT_S = 180

# codex takes image *paths*; the daemon downloads them first. Cap matches the
# picker's own limit so a runaway history can't turn one reply into a hundred
# downloads on the user's machine.
_MAX_IMAGES = 9


class CodexDaemonAdapter:
    def __init__(
        self,
        *,
        user_id: str,
        model: str = "",
        timeout_s: int = DEFAULT_TEXT_TIMEOUT_S,
        dispatch: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
        scope_resolver: Optional[Callable[[str], Awaitable[int]]] = None,
    ) -> None:
        self.user_id = str(user_id)
        self.model = model or ""
        self.timeout_s = int(timeout_s)
        self._dispatch = dispatch or dispatch_to_daemon
        self._scope = scope_resolver or resolve_personal_scope_id

    async def call(
        self,
        composed: ComposedSystemPrompt,
        messages: List[Dict[str, Any]],
        *,
        tool_choice: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if composed.tools:
            names = [
                t.get("function", {}).get("name", "?")
                for t in composed.tools
                if isinstance(t, dict)
            ]
            raise CodexLocalError(
                "tools_unsupported",
                f"codex exec has no function calling; agent binds tools {names[:5]}",
            )
        prompt, image_urls = flatten_for_codex(composed.system_message, messages)
        scope_id = await self._scope(self.user_id)
        payload = {
            "prompt": prompt,
            "model": (composed.model or self.model or "").strip(),
            "image_urls": image_urls[:_MAX_IMAGES],
            "timeout_s": self.timeout_s,
        }
        try:
            result = await self._dispatch(
                user_id=self.user_id,
                scope_id=scope_id,
                kind="text",
                payload=payload,
                timeout_s=self.timeout_s,
            )
        except DaemonOfflineError as exc:
            raise CodexLocalError("daemon_offline", str(exc)) from exc
        except TimeoutError as exc:
            raise CodexLocalError("timeout", str(exc)) from exc
        except RuntimeError as exc:
            raise from_daemon_error(str(exc)) from exc

        text = result.get("text")
        if not isinstance(text, str) or not text:
            raise CodexLocalError("codex_no_output", "daemon returned no text")
        raw_usage = result.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        prompt_tokens = int(usage.get("input_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or 0)
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": text,
                        "tool_calls": [],
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "prompt_tokens_details": {
                    "cached_tokens": int(usage.get("cached_input_tokens") or 0)
                },
            },
        }


__all__ = ["CodexDaemonAdapter", "DEFAULT_TEXT_TIMEOUT_S"]
