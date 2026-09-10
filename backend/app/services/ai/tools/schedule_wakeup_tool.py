"""ScheduleWakeup — the agent arms a one-time wake-up on the issue it is
working (phase 2b-2 §3).

The alternative it replaces is polling: an agent waiting on a render, a
download or a human takes a turn, finds nothing, and burns another turn. A
wake-up costs one row in ``user_schedules``; when it fires, the master
scheduler delivers the note back to this issue — onto the inbox if a run is
live, as a fresh turn if the issue is idle (``deliver_or_dispatch``).

The tool is exposed ONLY on an issue's ROOT run. A sub-agent that armed a
wake-up would resume a conversation it is not the owner of.

Every refusal is a tool RESULT (``{"error": ...}``), never a raise: a bad time
is something the model can correct on its next call, and a raise would end the
turn instead of letting it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

# The tool name as the model sees it / the runner dispatches on.
SCHEDULE_WAKEUP_TOOL_NAME = "ScheduleWakeup"

# How many wake-ups one run may arm. Three is enough for "check back in an
# hour, then tomorrow, then give up" and small enough that a confused agent
# cannot fill the scheduler with its own future turns.
MAX_WAKEUPS_PER_RUN = 3

# How far ahead a wake-up may be armed. The API path imports THIS constant
# (``schedules_router``), so the ceiling the model is told about and the one a
# human is held to are the same number.
MAX_WAKEUP_HORIZON = timedelta(days=30)

_DESCRIPTION = (
    "Schedule a one-time wake-up for this issue; when it fires you will "
    "receive the note as a message. Use it to wait for long external work "
    "instead of polling."
)


def schedule_wakeup_spec() -> dict[str, Any]:
    """OpenAI function-calling spec for ScheduleWakeup (model-facing)."""
    return {
        "type": "function",
        "function": {
            "name": SCHEDULE_WAKEUP_TOOL_NAME,
            "description": _DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "at": {
                        "type": "string",
                        "description": (
                            "Absolute time to wake up, ISO-8601 (e.g. "
                            "2026-09-11T09:00:00+00:00). Takes precedence "
                            "over delay_minutes."
                        ),
                    },
                    "delay_minutes": {
                        "type": "integer",
                        "description": "Wake up this many minutes from now.",
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "What you want to be told when it fires — you "
                            "will receive this text as a message."
                        ),
                    },
                },
                "required": ["note"],
            },
        },
    }


def make_schedule_wakeup_handler(
    *, issue_id: int, user_id: str
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Per-turn handler bound to one issue and its owner.

    The per-run budget lives in this closure because the closure IS the run:
    the chat service builds one per turn, next to the ``AgentRunner`` it
    belongs to. ``recorder`` arrives at call time — the run does not exist yet
    when the tool is registered — and supplies both the run id stamped on the
    row and the transcript the ``schedule_set`` event lands on.
    """
    armed = 0

    async def handler(args: dict[str, Any], recorder: Any = None) -> dict[str, Any]:
        nonlocal armed

        note = str((args or {}).get("note") or "").strip()
        if not note:
            return {"error": "note is required"}

        fire_at, why_not = _resolve_fire_at(args or {})
        if fire_at is None:
            return {"error": why_not}

        # Counted only once everything the model controls is valid, so a
        # rejected call never eats into the budget it could not have spent.
        if armed >= MAX_WAKEUPS_PER_RUN:
            return {"error": "too_many_wakeups"}

        run_id = getattr(recorder, "run_id", None)
        row = {
            "user_id": str(user_id),
            "name": note[:200],
            "cron_expr": None,
            "task_type": "issue_wakeup",
            "payload": {
                "issue_id": int(issue_id),
                "text": note,
                # ``once`` is what the mig-461 CHECK reads to permit a NULL
                # cron; without it Postgres rejects the row outright.
                "once": True,
                "created_by": "agent",
                "run_id": str(run_id) if run_id is not None else None,
            },
            "next_fire_at": fire_at,
        }
        try:
            schedule_id = await _insert_wakeup_row(row)
        except Exception as exc:  # noqa: BLE001 — a result the model reads
            logger.opt(exception=True).warning(
                f"[ScheduleWakeup] issue {issue_id}: insert failed: {exc}"
            )
            return {"error": f"ScheduleWakeup failed: {exc.__class__.__name__}"}

        armed += 1
        fire_at_iso = fire_at.isoformat()
        from app.services.ai.runner.events import emit

        await emit(
            recorder,
            "schedule_set",
            {"schedule_id": schedule_id, "fire_at": fire_at_iso, "note": note},
        )
        logger.info(
            f"[ScheduleWakeup] issue {issue_id}: wake-up {schedule_id} armed "
            f"for {fire_at_iso}"
        )
        return {"schedule_id": schedule_id, "fire_at": fire_at_iso}

    return handler


def _resolve_fire_at(args: dict[str, Any]) -> tuple[Optional[datetime], str]:
    """``(fire_at, refusal)`` — exactly one of the two is meaningful.

    ``at`` wins over ``delay_minutes``: a model that gives both has an
    absolute time in mind and the delay is the leftover of an earlier draft.
    A naive ``at`` is read as UTC rather than refused — omitting the offset is
    the commonest shape a model produces, and refusing it teaches nothing."""
    now = datetime.now(timezone.utc)
    raw_at = args.get("at")
    if raw_at not in (None, ""):
        try:
            parsed = datetime.fromisoformat(str(raw_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None, "at must be an ISO-8601 timestamp"
        fire_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    elif args.get("delay_minutes") not in (None, ""):
        try:
            minutes = int(args["delay_minutes"])
        except (TypeError, ValueError):
            return None, "delay_minutes must be a whole number of minutes"
        fire_at = now + timedelta(minutes=minutes)
    else:
        return None, "at or delay_minutes required"

    if fire_at <= now:
        return None, "fire_at must be in the future"
    if fire_at > now + MAX_WAKEUP_HORIZON:
        return None, "fire_at must be within 30 days"
    return fire_at, ""


async def _insert_wakeup_row(row: dict[str, Any]) -> str:
    """Insert the ``user_schedules`` row and return its id as a string.

    Its own function so the handler's decisions can be tested without a
    database — and because ``user_schedules.id`` is a UUID, which travels as a
    string everywhere downstream (events, ``meta.source``, the API)."""
    from sqlalchemy import insert

    from app.db.session import write_scope
    from app.models import UserSchedules

    async with write_scope() as session:
        created = (
            await session.execute(
                insert(UserSchedules).values(**row).returning(UserSchedules.id)
            )
        ).scalar_one()
    return str(created)


__all__ = [
    "MAX_WAKEUPS_PER_RUN",
    "MAX_WAKEUP_HORIZON",
    "SCHEDULE_WAKEUP_TOOL_NAME",
    "make_schedule_wakeup_handler",
    "schedule_wakeup_spec",
]
