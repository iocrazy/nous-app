"""Drive a single agent turn with tool-call resolution."""

from __future__ import annotations

import json
from typing import Any

from app.schemas.ai_library import ComposedSystemPrompt
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
    ) -> dict[str, Any]:
        messages = list(user_messages)
        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await self.adapter.call(composed, messages)
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
