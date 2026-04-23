"""Drive a single agent turn with tool-call resolution.

Optional ``recorder`` (:class:`RunRecorder`) — when passed, this runner
refreshes its heartbeat between iterations, polls ``cancel_requested``,
forwards token usage, and records skill invocations. All telemetry
failures are swallowed inside RunRecorder so the agent run itself
never breaks on a dead telemetry path.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.run_recorder import RunRecorder
from app.services.skill_tool_service import SkillToolService

MAX_TOOL_ITERATIONS = 5


class AgentRunner:
    def __init__(self, adapter: Any, skill_tool: SkillToolService) -> None:
        self.adapter = adapter
        self.skill_tool = skill_tool

    async def run_turn(
        self,
        composed: ComposedSystemPrompt,
        user_messages: list[dict],
        *,
        recorder: Optional[RunRecorder] = None,
    ) -> dict[str, Any]:
        messages = list(user_messages)
        for _ in range(MAX_TOOL_ITERATIONS):
            if recorder is not None:
                await recorder.heartbeat()
                if await recorder.check_cancelled():
                    return {"content": "", "raw": None, "cancelled": True}

            resp = await self.adapter.call(composed, messages)

            if recorder is not None:
                usage = resp.get("usage") or {}
                recorder.record_usage(
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                )

            msg = resp["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                # msg.get("content") can be None (e.g. Claude emits null
                # content on a pure-tool-use turn). The `or ""` guarantees
                # the contract — callers always receive a str.
                return {"content": msg.get("content") or "", "raw": resp}
            # Append assistant tool-call stub
            messages.append(msg)
            # Resolve each tool call
            for call in tool_calls:
                fn = call.get("function") or {}
                if fn.get("name") != "Skill":
                    # Unknown tool — skip (caller-provided tools handled elsewhere in future)
                    continue
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                if recorder is not None and args.get("skill"):
                    recorder.record_skill(str(args["skill"]))
                result = await self.skill_tool.execute(args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": "Skill",
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        return {"content": "", "raw": None, "error": "max_tool_iterations_exceeded"}
