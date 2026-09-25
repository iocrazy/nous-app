"""Platform-admin point adjustments, shared by both admin entry points.

``POST /admin/credits/adjust`` (admin console) and ``POST /points/admin/adjust``
(the user app's Billing page, platform admins only) do the same thing and
used to do it two different wrong ways:

- ``/admin/credits/adjust`` sent every amount to ``PointsService.add_points``,
  which refuses ``amount <= 0`` by returning ``{"success": False}``. The route
  never read that flag: a negative adjustment (the console's form says
  "Positive to add, negative to deduct") answered ``200 ok`` with
  ``new_balance: null``, wrote an audit entry, and moved no points.
- ``/points/admin/adjust`` debited with a read-modify-write across three
  transactions and ledgered the requested amount even when the balance was
  clamped at zero.

Both now call :func:`admin_adjust_team_points`: credits go through
``add_points`` (server-side increment), debits through
``PointsRepository.debit_points_clamped`` (row lock, clamp at zero, ledger the
amount actually taken). An unknown or non-numeric team is refused before any
write instead of surfacing as a foreign-key 500 from the quota insert.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.repositories.points_repository import get_points_repository
from app.services.billing.points_service import PointsService

ADMIN_ADJUST_TYPE = "admin_adjust"


class AdminAdjustError(ValueError):
    """The adjustment was refused before anything was written."""


class TeamNotFoundError(AdminAdjustError):
    """``team_id`` is not a number or names no team."""


def parse_team_id(raw: Any) -> int | None:
    """The BIGINT behind ``raw``, or ``None`` when it is not a positive integer."""
    text = str(raw).strip()
    if not text.isdigit():
        return None
    value = int(text)
    return value if 0 < value < 2**63 else None


async def existing_team_ids(team_ids: list[int]) -> set[int]:
    """Which of ``team_ids`` name a row in ``teams``."""
    if not team_ids:
        return set()
    from app.db.session import read_scope
    from app.models import Teams

    async with read_scope() as session:
        rows = await session.execute(select(Teams.id).where(Teams.id.in_(team_ids)))
        return {int(v) for v in rows.scalars().all()}


async def require_team(raw_team_id: Any) -> str:
    """``raw_team_id`` normalised to its decimal string, or TeamNotFoundError."""
    team_id = parse_team_id(raw_team_id)
    if team_id is None or team_id not in await existing_team_ids([team_id]):
        raise TeamNotFoundError(f"Team {raw_team_id} not found")
    return str(team_id)


async def admin_adjust_team_points(
    team_id: str,
    amount: int,
    description: str,
    user_id: str,
) -> dict[str, Any]:
    """Add (``amount > 0``) or take (``amount < 0``) points; zero is refused.

    ``team_id`` must already have passed :func:`require_team`.

    Returns ``{"success": True, "new_balance": int, "applied": int}`` where
    ``applied`` is the signed change that actually happened (a debit larger
    than the balance stops at zero).
    """
    if amount == 0:
        raise AdminAdjustError("Amount must not be zero")

    if amount > 0:
        result = await PointsService().add_points(
            team_id=team_id,
            amount=amount,
            type=ADMIN_ADJUST_TYPE,
            description=description,
            user_id=user_id,
        )
        if not result.get("success"):
            raise RuntimeError(f"add_points refused {amount} for team {team_id}")
        return {
            "success": True,
            "new_balance": result["new_balance"],
            "applied": amount,
        }

    repo = get_points_repository()
    debit = await repo.debit_points_clamped(
        team_id,
        -amount,
        user_id=user_id,
        type=ADMIN_ADJUST_TYPE,
        description=description,
    )
    if debit is None:
        # A real team with no quota row yet: its balance is 0, so there is
        # nothing to take. The row is NOT created here — first provisioning
        # decides the welcome bonus (``ensure_team_quota``), not an admin debit.
        return {"success": True, "new_balance": 0, "applied": 0}
    return {
        "success": True,
        "new_balance": debit["new_balance"],
        "applied": -debit["debited"],
    }
