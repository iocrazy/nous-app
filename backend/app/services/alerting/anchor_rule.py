"""Get-or-create the system ``alert_rules`` row that ``alert_history`` rows hang off.

``alert_history.rule_id`` is NOT NULL, so a system writer that is not driven
by a user-defined rule (the hourly agent-cost anomaly sweep, the scope
resolver's denied audit) still needs a rule row to point at. Each writer owns
one, identified by its ``name``.

The admin ``/alerts/check`` evaluator skips any ``metric_type`` it does not
know, so an *active* anchor rule is never evaluated as a threshold rule — the
flag only decides how the rule reads on the Alerts page.

Get-then-insert is not atomic: two first-ever writers racing can each create
a row. Harmless (the history rows still render, keyed by ``rule_name``) and
the same trade-off the anomaly sweep has always made.
"""

from __future__ import annotations

from sqlalchemy import insert, select

from app.db import session as db_session
from app.models import AlertRules


async def ensure_anchor_rule(
    *,
    name: str,
    metric_type: str,
    threshold: float,
    is_active: bool,
    condition: str = "gte",
    window_minutes: int = 60,
) -> int:
    """Return the id of the rule named ``name``, creating it on first use.

    The scopes are looked up on ``app.db.session`` at call time (not bound at
    import) so callers' tests that patch that module keep working."""
    async with db_session.read_scope() as session:
        rule_id = (
            await session.execute(
                select(AlertRules.id).where(AlertRules.name == name).limit(1)
            )
        ).scalar()
    if rule_id is not None:
        return int(rule_id)

    async with db_session.write_scope() as session:
        created = (
            await session.execute(
                insert(AlertRules)
                .values(
                    name=name,
                    metric_type=metric_type,
                    condition=condition,
                    threshold=threshold,
                    window_minutes=window_minutes,
                    notification_channel="discord",
                    is_active=is_active,
                )
                .returning(AlertRules.id)
            )
        ).scalar()
    return int(created)
