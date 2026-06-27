"""Scheduled scanner that drives agent broadcast messages into team chat channels.

Runs every 2 minutes via @DBOS.scheduled. Delegates entirely to
scan_and_broadcast() from the chat service layer — this module is
intentionally thin (no logic, just the DBOS schedule wrapper).

The workflow MUST NOT raise: a failure would poison the DBOS scheduler
and suppress all future ticks. All errors are caught, logged via loguru
f-string, and a safe fallback dict is returned instead.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from dbos import DBOS
from loguru import logger


@DBOS.scheduled("0 */2 * * * *")  # every 2 minutes (6-field cron with seconds)
@DBOS.workflow()
async def task_broadcast_workflow(
    scheduled_at: datetime, actual_at: datetime
) -> dict[str, Any]:
    """Scan pending agent broadcast messages and post them to chat channels."""
    from app.services.chat.agent_broadcast import scan_and_broadcast

    try:
        result: dict[str, Any] = await scan_and_broadcast()
        if result.get("messages_posted"):
            logger.info(f"[agent-broadcast] {result}")
        return result
    except Exception as exc:
        logger.error(f"[agent-broadcast] scan_and_broadcast failed: {exc}")
        return {"channels_scanned": 0, "messages_posted": 0, "error": str(exc)}
