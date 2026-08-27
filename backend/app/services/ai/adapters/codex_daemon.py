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

from loguru import logger

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.codex.daemon_dispatch import DaemonOfflineError, dispatch_to_daemon
from app.services.codex.errors import CodexLocalError, from_daemon_error
from app.services.codex.flatten import flatten_for_codex
from app.services.codex.personal_scope import resolve_personal_scope_id

DEFAULT_TEXT_TIMEOUT_S = 180

# The backend waits LONGER than the daemon runs, on purpose. Both legs get a
# timeout: the daemon caps ``codex exec`` with the payload's ``timeout_s``,
# the backend caps the result subscription with the dispatch's. Setting them
# equal makes them race — and the backend winning is the bad outcome: the
# caller gets a generic ``timeout`` instead of the typed code the daemon was
# about to publish (``codex_not_logged_in``, ``codex_no_output``, …). The
# grace period keeps the daemon's answer first, so the fallback only fires
# when the daemon really has gone silent.
DISPATCH_GRACE_S = 30

# codex takes image *paths*; the daemon downloads every one of them before it
# can start. This is a LAST-RESORT backstop, not the real budget: the chat path
# already caps images upstream at ``history_image_replay.REPLAY_MAX_IMAGES``
# (4), so in practice this never trims anything. It exists so a caller that
# skips that replay logic still cannot turn one reply into a hundred downloads
# on the user's machine. Trimming here is logged, never silent — the prompt
# text still refers to the images that were dropped, so the model would answer
# about pictures it never received and nothing else would say why.
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
        if len(image_urls) > _MAX_IMAGES:
            logger.warning(
                "[codex-local] dropping {} of {} images for user={} "
                "(backstop cap {}); the prompt still references them",
                len(image_urls) - _MAX_IMAGES,
                len(image_urls),
                self.user_id,
                _MAX_IMAGES,
            )
        scope_id = await self._scope(self.user_id)
        payload = {
            "prompt": prompt,
            # ``self.model`` ONLY — never ``composed.model``. The latter is
            # ``agents.model``, which for this path holds the catalog row's
            # display name ("Codex (Local)"), not something codex can run:
            # letting it win would ship `codex exec --model "Codex (Local)"`.
            # ``self.model`` is the row's ``actual_model``, threaded in at
            # construction; "" means "whatever the user's own codex defaults
            # to", which is a valid answer here in a way it is not for a
            # hosted provider.
            "model": (self.model or "").strip(),
            "image_urls": image_urls[:_MAX_IMAGES],
            "timeout_s": self.timeout_s,
        }
        try:
            result = await self._dispatch(
                user_id=self.user_id,
                scope_id=scope_id,
                kind="text",
                payload=payload,
                timeout_s=self.timeout_s + DISPATCH_GRACE_S,
            )
        except DaemonOfflineError as exc:
            raise CodexLocalError("daemon_offline", str(exc)) from exc
        except TimeoutError as exc:
            raise CodexLocalError("timeout", str(exc)) from exc
        except CodexLocalError:
            # Already typed — re-wrapping through ``from_daemon_error`` below
            # would nest one marker inside another and lose the real code
            # (``CodexLocalError`` is itself a ``RuntimeError``).
            raise
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


__all__ = ["CodexDaemonAdapter", "DEFAULT_TEXT_TIMEOUT_S", "DISPATCH_GRACE_S"]
