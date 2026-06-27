"""Agent Broadcast service (CHAT-AGENT-05, PERM-11, SEC-AGENT-05).

Provides:
  build_broadcast_summary(total, by_kind) -> str
    Pure function. Takes ONLY aggregate counts + kind names (system enums).
    Structurally incapable of leaking user-scope private data — SEC-AGENT-05
    boundary is enforced by the function signature itself.

  scan_and_broadcast() -> {"channels_scanned": int, "messages_posted": int}
    Scans all candidate channels, gates on PERM-11 (auto_broadcast cap),
    posts at most ONE templated summary per channel since the last watermark.

Design invariants:
  - PERM-11: only agents with auto_broadcast=True AND enabled=True AND
    allows_team(team_id) may post. Fail-closed (ChatCaps defaults all False).
  - No-backfill: when a channel has no watermark yet, set watermark=now and
    post NOTHING (never dump historical completions on first run).
  - Idempotent: watermark advances to max_completed_at ONLY after a
    successful send. A crash before the post leaves watermark unchanged so
    the next scan re-evaluates the same window.
  - Anti-loop: messages carry from_bot_agent_id → dispatch_summons returns
    [] immediately for bot-authored messages (no agent recursion).
  - Bounded: at most ONE summary per channel per scan, regardless of how
    many tasks completed.
  - Per-channel isolation: each channel iteration is wrapped in try/except
    so one bad channel never aborts the rest of the scan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.repositories.agent_repository import get_agent_repository
from app.repositories.chat_broadcast_repository import get_broadcast_repository
from app.repositories.chat_repository import get_chat_repository
from app.services.ai.permissions.agent_chat_caps import agent_chat_caps

# ── SEC-AGENT-05 containment boundary ────────────────────────────────────────
# The function signature is the boundary: its ONLY inputs are an integer count
# and a dict of (kind_name: str → count: int). There is no path to pass task
# titles, subtitle, metadata, or any resource content in. LLM is never invoked.


def build_broadcast_summary(total: int, by_kind: dict[str, int]) -> str:
    """Return a team-shareable broadcast summary string.

    Inputs: ONLY aggregate counts + task_kind enum names (system strings).
    Output example: "✅ 5 task(s) completed · 3 workflow, 2 download"

    This is the SEC-AGENT-05 containment boundary. It is impossible for a
    caller to inject subtitle/metadata/resource content via this interface.
    No LLM. No resource reads. No dynamic template expansion.
    """
    header = f"✅ {total} task(s) completed"
    if not by_kind:
        return header
    breakdown = ", ".join(f"{count} {kind}" for kind, count in by_kind.items())
    return f"{header} · {breakdown}"


# ── Orchestration ─────────────────────────────────────────────────────────────


async def scan_and_broadcast() -> dict[str, Any]:
    """Scan all candidate channels and post a task-completion summary.

    Returns {"channels_scanned": int, "messages_posted": int}.

    See module docstring for full design invariants (PERM-11, no-backfill,
    idempotent watermark, anti-loop, per-channel isolation).
    """
    repo = get_broadcast_repository()
    chat_repo = get_chat_repository()
    ar = get_agent_repository()

    channels_scanned = 0
    messages_posted = 0

    candidates = await repo.list_broadcast_candidate_channels()

    for ch in candidates:
        channels_scanned += 1
        channel_id: int = ch["channel_id"]
        team_id: int = ch["team_id"]
        agent_ids: list[str] = ch["agent_ids"]

        try:
            # ── PERM-11: filter to auto_broadcast-capable agents ──────────
            bc_agents: list[dict[str, Any]] = []
            for aid in agent_ids:
                agent = await ar.get_by_id(aid)
                if agent is None:
                    continue
                caps = agent_chat_caps(agent)
                if caps.auto_broadcast and caps.enabled and caps.allows_team(team_id):
                    bc_agents.append(agent)

            if not bc_agents:
                continue

            # ── Watermark check ───────────────────────────────────────────
            wm = await repo.get_watermark(channel_id)

            if wm is None:
                # First run for this channel — set watermark to now, post NOTHING.
                # On the next scan, only tasks completed *after* this moment count.
                await repo.set_watermark(channel_id, datetime.now(timezone.utc))
                continue

            # ── Count completed workflows since watermark ─────────────────
            # Counts are scoped to the channel's team via a JOIN on team_members
            # inside the repo (team_id, not a user_id list) — no array-bind.
            counts = await repo.completed_workflow_counts_since(team_id, wm)
            if counts["total"] <= 0:
                continue

            # ── Build safe, templated summary (SEC-AGENT-05) ─────────────
            text = build_broadcast_summary(counts["total"], counts["by_kind"])

            # ── Post once, authored by the first eligible broadcast agent ─
            # from_bot_agent_id → dispatch_summons returns [] immediately
            # (anti-loop guard in chat_service.dispatch_summons).
            agent = bc_agents[0]
            await chat_repo.send_message(
                channel_id=channel_id,
                sender_id=None,
                sender_type="agent",
                content_type="text",
                body={"text": text},
                reply_to_id=None,
                from_bot_agent_id=str(agent["id"]),
            )

            # ── Advance watermark ONLY after successful post ──────────────
            # A crash before this line leaves watermark unchanged → the next
            # scan will re-evaluate the same window (safe to re-post once on
            # retry; better than silently skipping completions).
            await repo.set_watermark(channel_id, counts["max_completed_at"])
            messages_posted += 1

        except Exception as exc:
            logger.error(
                f"[agent_broadcast] channel_scan_error: "
                f"channel_id={channel_id} team_id={team_id} error={exc!r}"
            )
            # Per-channel isolation: one bad channel must not abort the scan.

    return {"channels_scanned": channels_scanned, "messages_posted": messages_posted}
