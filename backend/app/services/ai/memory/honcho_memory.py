"""Honcho user-model client (Phase 4 M5 — Canvas+AI plan).

Thin REST wrapper around a self-hosted Honcho v3 instance so the rest
of the backend never speaks Honcho's API directly:

    add_chat_turn()   — ingest one user/assistant exchange; Honcho's
                        deriver builds the psychological user model
                        (peer cards / working representation) async
    get_user_context()— dialectic query about a user ("what does this
                        user prefer?"); returns None until an
                        embedding provider is configured server-side

Mapping (canvas plan Phase 4): Peer = ``user-{id}`` / ``agent-{id}``.
Workspace is a deployment-level namespace (``HONCHO_WORKSPACE_ID``,
default ``mediahub``) — the plan's Workspace=team mapping needs team
context threaded into the harvest path first; tracked as a TODO so the
single-workspace default stays an explicit decision, not an accident.

Safety contract (same as graph_memory): every public method swallows +
logs failures and returns a falsy value. A Honcho outage must never
break chat or the L1/graph write paths.

Configuration (env):
    FEATURE_HONCHO_MEMORY  — master flag, default false
    HONCHO_BASE_URL        — e.g. http://192.168.50.9:18000
    HONCHO_WORKSPACE_ID    — namespace, default ``mediahub``
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}

DEFAULT_WORKSPACE = "mediahub"
_REQUEST_TIMEOUT_S = 10.0
# Honcho's POST endpoints are get-or-create; 409 covers any
# already-exists race on older builds.
_OK_STATUSES = {200, 201, 409}


@dataclass(frozen=True)
class HonchoMemoryConfig:
    enabled: bool = False
    base_url: str = ""
    workspace_id: str = DEFAULT_WORKSPACE

    @classmethod
    def from_env(cls) -> "HonchoMemoryConfig":
        return cls(
            enabled=os.getenv("FEATURE_HONCHO_MEMORY", "").strip().lower() in _TRUTHY,
            base_url=os.getenv("HONCHO_BASE_URL", "").strip().rstrip("/"),
            workspace_id=os.getenv("HONCHO_WORKSPACE_ID", "").strip()
            or DEFAULT_WORKSPACE,
        )

    def operative(self) -> bool:
        """True when the flag is on AND a server address exists."""
        return self.enabled and bool(self.base_url)


@dataclass
class HonchoMemoryService:
    """Lazy httpx client; inject ``client`` (httpx.AsyncClient) in tests."""

    config: HonchoMemoryConfig = field(default_factory=HonchoMemoryConfig.from_env)
    client: Optional[httpx.AsyncClient] = None
    # Get-or-create calls already made this process — Honcho treats the
    # POSTs as idempotent, this just trims 3 round-trips per turn.
    _ensured: set[str] = field(default_factory=set)

    def _get_client(self) -> Optional[httpx.AsyncClient]:
        if self.client is not None:
            return self.client if self.config.enabled else None
        if not self.config.operative():
            return None
        self.client = httpx.AsyncClient(
            base_url=self.config.base_url, timeout=_REQUEST_TIMEOUT_S
        )
        return self.client

    async def _post_ok(self, client: httpx.AsyncClient, path: str, json: dict) -> bool:
        response = await client.post(path, json=json)
        if response.status_code not in _OK_STATUSES:
            logger.warning(
                "[honcho] POST %s -> %s: %s",
                path,
                response.status_code,
                response.text[:200],
            )
            return False
        return True

    async def _ensure(
        self, client: httpx.AsyncClient, key: str, path: str, json: dict
    ) -> bool:
        if key in self._ensured:
            return True
        ok = await self._post_ok(client, path, json)
        if ok:
            self._ensured.add(key)
        return ok

    async def add_chat_turn(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
        workspace_id: Optional[str] = None,
    ) -> bool:
        """Ingest one exchange. Returns True when the messages landed;
        False on any failure or when disabled/inoperative.

        ``workspace_id`` overrides the config default — the harvest
        path passes ``team-{team_id}`` when the session carries team
        context (canvas plan: Workspace=team), else the deployment
        default applies."""
        client = self._get_client()
        if client is None:
            return False
        if not (user_message.strip() or assistant_message.strip()):
            return False

        workspace = workspace_id or self.config.workspace_id
        user_peer = f"user-{user_id}"
        agent_peer = f"agent-{agent_id}"
        session = f"session-{session_id}"
        base = f"/v3/workspaces/{workspace}"
        try:
            if not await self._ensure(
                client, f"w:{workspace}", "/v3/workspaces", {"id": workspace}
            ):
                return False
            for peer in (user_peer, agent_peer):
                if not await self._ensure(
                    client, f"p:{peer}", f"{base}/peers", {"id": peer}
                ):
                    return False
            if not await self._ensure(
                client, f"s:{session}", f"{base}/sessions", {"id": session}
            ):
                return False

            messages = []
            if user_message.strip():
                messages.append({"peer_id": user_peer, "content": user_message})
            if assistant_message.strip():
                messages.append({"peer_id": agent_peer, "content": assistant_message})
            return await self._post_ok(
                client,
                f"{base}/sessions/{session}/messages",
                {"messages": messages},
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "[honcho] add_chat_turn failed (user=%s session=%s)",
                user_id,
                session_id,
            )
            return False

    async def get_user_representation(
        self,
        *,
        user_id: str,
        workspace_id: Optional[str] = None,
        max_chars: int = 2000,
    ) -> Optional[str]:
        """Working representation of ``user_id`` (deriver observations).

        Unlike the dialectic ``get_user_context`` this is a pure DB read
        on the Honcho side — no LLM call — so it's cheap enough to run
        on every chat turn for prompt injection (Phase 4 L2). None on
        any failure, when disabled, or when the peer has no
        observations yet.
        """
        client = self._get_client()
        if client is None:
            return None
        workspace = workspace_id or self.config.workspace_id
        try:
            response = await client.post(
                f"/v3/workspaces/{workspace}/peers/user-{user_id}/representation",
                json={},
            )
            if response.status_code != 200:
                logger.warning(
                    "[honcho] representation -> %s: %s",
                    response.status_code,
                    response.text[:200],
                )
                return None
            payload: Any = response.json()
            rep = payload.get("representation") if isinstance(payload, dict) else None
            rep = str(rep).strip() if rep else ""
            if not rep:
                return None
            return rep[:max_chars]
        except Exception:  # noqa: BLE001
            logger.exception("[honcho] representation fetch failed (user=%s)", user_id)
            return None

    # ------------------------------------------------------------------
    # Memory management (user-facing panel) — conclusions = the deriver's
    # individual observations, addressable by stable id.
    # ------------------------------------------------------------------

    async def list_conclusions(
        self,
        *,
        user_id: str,
        workspace_id: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        """Observations ABOUT ``user_id``. Empty list on any failure.

        Results are re-filtered client-side on ``observed_id`` so a drift
        in Honcho's server-side filter semantics can never leak another
        peer's observations into the management panel.
        """
        client = self._get_client()
        if client is None:
            return []
        workspace = workspace_id or self.config.workspace_id
        peer = f"user-{user_id}"
        try:
            response = await client.post(
                f"/v3/workspaces/{workspace}/conclusions/list",
                json={"filter": {"observed": peer}},
            )
            if response.status_code != 200:
                logger.warning(
                    "[honcho] conclusions list -> %s: %s",
                    response.status_code,
                    response.text[:200],
                )
                return []
            items = (response.json() or {}).get("items") or []
            scoped = [i for i in items if i.get("observed_id") == peer]
            return [
                {
                    "id": i.get("id"),
                    "content": i.get("content"),
                    "created_at": i.get("created_at"),
                    "session_id": i.get("session_id"),
                }
                for i in scoped[:limit]
            ]
        except Exception:  # noqa: BLE001
            logger.exception("[honcho] conclusions list failed (user=%s)", user_id)
            return []

    async def get_conclusion(
        self, *, conclusion_id: str, workspace_id: Optional[str] = None
    ) -> Optional[dict]:
        """Single observation by id; None when missing or on failure."""
        client = self._get_client()
        if client is None:
            return None
        workspace = workspace_id or self.config.workspace_id
        try:
            response = await client.get(
                f"/v3/workspaces/{workspace}/conclusions/{conclusion_id}"
            )
            if response.status_code != 200:
                return None
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except Exception:  # noqa: BLE001
            logger.exception("[honcho] conclusion fetch failed (%s)", conclusion_id)
            return None

    async def delete_conclusion(
        self, *, conclusion_id: str, workspace_id: Optional[str] = None
    ) -> bool:
        """Delete one observation. Caller is responsible for the
        ownership check (router verifies observed_id == the requesting
        user's peer before calling)."""
        client = self._get_client()
        if client is None:
            return False
        workspace = workspace_id or self.config.workspace_id
        try:
            response = await client.delete(
                f"/v3/workspaces/{workspace}/conclusions/{conclusion_id}"
            )
            if response.status_code not in (200, 204):
                logger.warning(
                    "[honcho] conclusion delete -> %s: %s",
                    response.status_code,
                    response.text[:200],
                )
                return False
            return True
        except Exception:  # noqa: BLE001
            logger.exception("[honcho] conclusion delete failed (%s)", conclusion_id)
            return False

    async def get_peer_card(
        self, *, user_id: str, workspace_id: Optional[str] = None
    ) -> Optional[list[str]]:
        """User-curated "About me" card lines; None when unset/failed."""
        client = self._get_client()
        if client is None:
            return None
        workspace = workspace_id or self.config.workspace_id
        try:
            response = await client.get(
                f"/v3/workspaces/{workspace}/peers/user-{user_id}/card"
            )
            if response.status_code != 200:
                return None
            card = (response.json() or {}).get("peer_card")
            return list(card) if card else None
        except Exception:  # noqa: BLE001
            logger.exception("[honcho] card fetch failed (user=%s)", user_id)
            return None

    async def set_peer_card(
        self,
        *,
        user_id: str,
        lines: list[str],
        workspace_id: Optional[str] = None,
    ) -> bool:
        """Replace the "About me" card. Empty list clears it."""
        client = self._get_client()
        if client is None:
            return False
        workspace = workspace_id or self.config.workspace_id
        try:
            response = await client.put(
                f"/v3/workspaces/{workspace}/peers/user-{user_id}/card",
                json={"peer_card": lines},
            )
            return response.status_code in _OK_STATUSES
        except Exception:  # noqa: BLE001
            logger.exception("[honcho] card set failed (user=%s)", user_id)
            return False

    async def forget_user(
        self, *, user_id: str, workspace_id: Optional[str] = None
    ) -> int:
        """Forget everything derived about the user: delete all their
        observations + clear the card. Raw chat messages are retained
        (deleting memory ≠ deleting chat history). Returns the number
        of observations deleted."""
        conclusions = await self.list_conclusions(
            user_id=user_id, workspace_id=workspace_id, limit=1000
        )
        deleted = 0
        for item in conclusions:
            cid = item.get("id")
            if cid and await self.delete_conclusion(
                conclusion_id=cid, workspace_id=workspace_id
            ):
                deleted += 1
        await self.set_peer_card(user_id=user_id, lines=[], workspace_id=workspace_id)
        return deleted

    async def get_user_context(
        self, *, user_id: str, query: str, timeout_s: float = 30.0
    ) -> Optional[str]:
        """Dialectic query about ``user_id``. None on any failure —
        including while the server lacks an embedding provider (its
        search_memory tool errors out server-side)."""
        client = self._get_client()
        if client is None or not query.strip():
            return None
        workspace = self.config.workspace_id
        try:
            response = await client.post(
                f"/v3/workspaces/{workspace}/peers/user-{user_id}/chat",
                json={"query": query},
                timeout=timeout_s,
            )
            if response.status_code != 200:
                logger.warning(
                    "[honcho] dialectic -> %s: %s",
                    response.status_code,
                    response.text[:200],
                )
                return None
            payload: Any = response.json()
            if isinstance(payload, dict):
                content = payload.get("content") or payload.get("response")
                return str(content) if content else None
            return str(payload) if payload else None
        except Exception:  # noqa: BLE001
            logger.exception("[honcho] dialectic query failed (user=%s)", user_id)
            return None


_service: Optional[HonchoMemoryService] = None


def get_honcho_memory_service() -> HonchoMemoryService:
    """Process-wide singleton, configured from env on first use."""
    global _service
    if _service is None:
        _service = HonchoMemoryService()
    return _service


__all__ = [
    "HonchoMemoryConfig",
    "HonchoMemoryService",
    "get_honcho_memory_service",
]
