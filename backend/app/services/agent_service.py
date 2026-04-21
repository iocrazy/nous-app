# backend/app/services/agent_service.py

"""
AgentService — LLM-powered AI agent calling with session management,
context truncation, retry logic, and usage logging.
"""

import asyncio
import time
from typing import Any

import httpx
from loguru import logger

from app.core.config import settings
from app.db.supabase_client import get_async_supabase_admin

# Backward-compatible module-level aliases.  All values are sourced from settings
# so operators can tune behaviour via env vars without code edits.
MAX_CONTEXT_TOKENS = settings.LLM_MAX_CONTEXT_TOKENS
MAX_HISTORY_MESSAGES = settings.LLM_MAX_HISTORY_MESSAGES
CACHE_TTL = settings.LLM_AGENT_CACHE_TTL_SECONDS


class AgentService:
    """Core service for calling AI agents via LLM API.

    Handles:
    - Agent config loading with TTL cache
    - Session creation/retrieval via AISessionService
    - Context-aware message history with token budget
    - LLM API calls with retry logic (timeout + rate-limit)
    - Persisting user/assistant messages to ai_messages
    - Logging token usage to ai_usage_logs
    """

    def __init__(self) -> None:
        self.api_url = settings.LLM_API_URL
        self.api_key = settings.LLM_API_KEY
        self.default_model = settings.LLM_MODEL
        self._client: httpx.AsyncClient | None = None
        # Cache: agent_id -> (agent_dict, loaded_at_timestamp)
        self._agent_cache: dict[str, tuple[dict, float]] = {}

    # ------------------------------------------------------------------
    # HTTP client
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """Return a shared async HTTP client, recreating if closed."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Agent loading with cache
    # ------------------------------------------------------------------

    async def load_agent(self, agent_id: str) -> dict:
        """Load agent config from cache or Supabase.

        Returns the agent row as a dict.
        Raises ValueError if the agent is not found or disabled.
        """
        now = time.monotonic()
        cached = self._agent_cache.get(agent_id)
        if cached is not None:
            agent_dict, loaded_at = cached
            if now - loaded_at < CACHE_TTL:
                return agent_dict

        supabase = await get_async_supabase_admin()
        response = (
            await supabase.table("ai_agents")
            .select("*")
            .eq("id", agent_id)
            .single()
            .execute()
        )
        agent = response.data
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")
        if not agent.get("enabled", True):
            raise ValueError(f"Agent is disabled: {agent_id}")

        self._agent_cache[agent_id] = (agent, now)
        return agent

    def invalidate_cache(self, agent_id: str) -> None:
        """Remove a specific agent from the cache."""
        self._agent_cache.pop(agent_id, None)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def call_agent(
        self,
        agent_id: str,
        message: str,
        *,
        user_id: str,
        session_id: str | None = None,
        project_id: str | None = None,
        team_id: str | None = None,
        context: dict | None = None,
    ) -> dict:
        """Call an AI agent with a user message.

        Steps:
        1. Load agent config (cached)
        2. Get or create session
        3. Build message list with token-budget context
        4. Call LLM with retry
        5. Persist messages
        6. Log usage

        Returns a dict with: content, session_id, agent_id,
        prompt_tokens, completion_tokens.
        """
        # 1. Load agent config
        agent = await self.load_agent(agent_id)

        # 2. Get or create session
        from app.services.ai_session_service import (
            AISessionService,
        )  # local import to avoid circular

        session_svc = AISessionService()
        if session_id:
            session = await session_svc.get_session(session_id, user_id)
        else:
            session = await session_svc.create_session(
                user_id=user_id,
                project_id=project_id,
                team_id=team_id,
            )
            session_id = session["id"]

        # 3. Build messages with context truncation
        messages = await self._build_messages(agent, session_id, message, context)

        # 4. Call LLM
        result = await self._call_llm(messages, agent)

        # 5. Save messages
        await self._save_messages(
            session_id=session_id,
            user_msg=message,
            assistant_msg=result["content"],
            agent_id=agent_id,
            usage=result["usage"],
        )

        # 6. Log usage
        await self._log_usage(
            user_id=user_id,
            team_id=team_id,
            project_id=project_id,
            session_id=session_id,
            agent_id=agent_id,
            model=agent.get("model", self.default_model),
            usage=result["usage"],
        )

        return {
            "content": result["content"],
            "session_id": session_id,
            "agent_id": agent_id,
            "prompt_tokens": result["usage"].get("prompt_tokens", 0),
            "completion_tokens": result["usage"].get("completion_tokens", 0),
        }

    # ------------------------------------------------------------------
    # Message building
    # ------------------------------------------------------------------

    async def _build_messages(
        self,
        agent: dict,
        session_id: str,
        user_message: str,
        context: dict | None,
    ) -> list[dict]:
        """Build the messages list with system prompt + history + user message.

        Applies a token budget so the total context never exceeds
        MAX_CONTEXT_TOKENS. History messages are included newest-first
        until the budget is exhausted.
        """
        system_prompt = self._build_system_prompt(agent, context)
        system_tokens = self._estimate_tokens(system_prompt)
        user_tokens = self._estimate_tokens(user_message)
        remaining = MAX_CONTEXT_TOKENS - system_tokens - user_tokens

        # Load recent history
        supabase = await get_async_supabase_admin()
        resp = (
            await supabase.table("ai_messages")
            .select("role, content")
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .limit(MAX_HISTORY_MESSAGES)
            .execute()
        )
        history_rows: list[dict] = resp.data or []

        # Select messages within token budget (newest-first traversal, then reverse)
        selected: list[dict] = []
        for row in history_rows:
            msg_tokens = self._estimate_tokens(row["content"])
            if remaining - msg_tokens < 0:
                break
            selected.append({"role": row["role"], "content": row["content"]})
            remaining -= msg_tokens

        # Reverse to chronological order (oldest first)
        selected.reverse()

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(selected)
        messages.append({"role": "user", "content": user_message})
        return messages

    def _build_system_prompt(self, agent: dict, context: dict | None) -> str:
        """Assemble the system prompt from persona, rules, and runtime context."""
        parts: list[str] = [agent["persona"]]

        for rule in agent.get("rules", []):
            if isinstance(rule, dict):
                rule_name = rule.get("name", "")
                rule_content = rule.get("content", "")
                parts.append(f"## Rule: {rule_name}\n{rule_content}")
            elif isinstance(rule, str) and rule.strip():
                parts.append(rule)

        if context:
            if context.get("genre"):
                parts.append(f"Story genre: {context['genre']}")
            if context.get("extra_instructions"):
                parts.append(str(context["extra_instructions"]))

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # LLM call with retry
    # ------------------------------------------------------------------

    async def _call_llm(self, messages: list[dict], agent: dict) -> dict:
        """Call the LLM chat completions endpoint.

        Retry strategy:
        - Up to 3 attempts total
        - HTTP 429: wait Retry-After seconds, then retry (1 retry)
        - TimeoutException: exponential back-off (2 retries)

        Returns dict with keys: content, usage (prompt_tokens, completion_tokens).
        """
        client = await self._get_client()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": agent.get("model", self.default_model),
            "messages": messages,
            "temperature": agent.get("temperature", 0.7),
            "max_tokens": agent.get("max_tokens", 4096),
        }

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await client.post(
                    f"{self.api_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    logger.warning(
                        f"LLM rate-limited, retrying after {retry_after}s (attempt {attempt + 1})"
                    )
                    await asyncio.sleep(retry_after)
                    continue
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage", {})
                return {
                    "content": data["choices"][0]["message"]["content"],
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                    },
                }
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < 2:
                    backoff = 2**attempt
                    logger.warning(
                        f"LLM request timed out, retrying in {backoff}s (attempt {attempt + 1})"
                    )
                    await asyncio.sleep(backoff)
                    continue
                break

        raise RuntimeError(
            f"LLM call failed after 3 attempts: {last_exc}"
        ) from last_exc

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token count: CJK ~1.5 chars/token, other ~4 chars/token."""
        cjk_count = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other_count = len(text) - cjk_count
        return max(1, int(cjk_count / 1.5 + other_count / 4))

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    async def _save_messages(
        self,
        session_id: str,
        user_msg: str,
        assistant_msg: str,
        agent_id: str,
        usage: dict,
    ) -> None:
        """Insert user + assistant messages and update session counters."""
        supabase = await get_async_supabase_admin()
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        rows = [
            {
                "session_id": session_id,
                "role": "user",
                "content": user_msg,
                "agent_id": None,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            },
            {
                "session_id": session_id,
                "role": "assistant",
                "content": assistant_msg,
                "agent_id": agent_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        ]

        insert_resp = await supabase.table("ai_messages").insert(rows).execute()
        if not insert_resp.data:
            logger.error(f"Failed to insert messages for session {session_id}")

        # Update session aggregate counters atomically.
        # The previous read-then-update pattern lost increments when two chat
        # calls on the same session ran concurrently (both read the same baseline,
        # each wrote baseline+2).  increment_ai_session_counters is a single-
        # statement Postgres function (see migration 122).
        total_tokens = prompt_tokens + completion_tokens
        try:
            await supabase.rpc(
                "increment_ai_session_counters",
                {
                    "p_session_id": session_id,
                    "p_tokens": total_tokens,
                    "p_messages": 2,
                },
            ).execute()
        except Exception as rpc_err:
            # Migration 122 not yet applied?  Fall back to the legacy path with a
            # warning so that behaviour degrades gracefully in older environments.
            logger.warning(
                f"increment_ai_session_counters RPC failed ({rpc_err}); "
                "falling back to non-atomic update"
            )
            session_resp = (
                await supabase.table("ai_sessions")
                .select("total_tokens, message_count")
                .eq("id", session_id)
                .single()
                .execute()
            )
            if session_resp.data:
                current = session_resp.data
                await (
                    supabase.table("ai_sessions")
                    .update(
                        {
                            "total_tokens": current.get("total_tokens", 0)
                            + total_tokens,
                            "message_count": current.get("message_count", 0) + 2,
                            "updated_at": "now()",
                        }
                    )
                    .eq("id", session_id)
                    .execute()
                )

    async def _log_usage(
        self,
        user_id: str,
        team_id: str | None,
        project_id: str | None,
        session_id: str,
        agent_id: str,
        model: str,
        usage: dict,
    ) -> None:
        """Insert a record into ai_usage_logs."""
        supabase = await get_async_supabase_admin()
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        row = {
            "user_id": user_id,
            "team_id": team_id,
            "project_id": project_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        resp = await supabase.table("ai_usage_logs").insert(row).execute()
        if not resp.data:
            logger.warning(f"Failed to log usage for session {session_id}")
