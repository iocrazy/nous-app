"""Get-or-create the system ``alert_rules`` row that ``alert_history`` rows hang off.

``alert_history.rule_id`` is NOT NULL, so a system writer that is not driven
by a user-defined rule (the hourly agent-cost anomaly sweep, the scope
resolver's denied audit) still needs a rule row to point at. Each writer owns
one, identified by its ``name``.

The admin ``/alerts/check`` evaluator skips any ``metric_type`` it does not
know, so an *active* anchor rule is never evaluated as a threshold rule — the
flag only decides how the rule reads on the Alerts page.

Anchor rows are the ones with ``created_by IS NULL`` (this helper never sets
it; the admin create endpoint always stamps the calling admin). Migration 495
makes them unique by name with a partial UNIQUE index, and the insert here is
``ON CONFLICT (name) WHERE created_by IS NULL DO NOTHING``: when two first-ever
writers race, the loser's insert returns no row and it re-selects the
winner's id. Before 495 this was a plain get-then-insert that could create
duplicate anchors under a race.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.db import session as db_session
from app.models import AlertRules

_ANCHOR_PREDICATE = "created_by IS NULL"


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
    by_name = select(AlertRules.id).where(AlertRules.name == name).limit(1)
    async with db_session.read_scope() as session:
        rule_id = (await session.execute(by_name)).scalar()
    if rule_id is not None:
        return int(rule_id)

    stmt = (
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
        .on_conflict_do_nothing(
            index_elements=[AlertRules.name],
            index_where=text(_ANCHOR_PREDICATE),
        )
        .returning(AlertRules.id)
    )
    async with db_session.write_scope() as session:
        created = (await session.execute(stmt)).scalar()
        if created is None:
            # Lost the race: the conflicting row is committed by now (ON
            # CONFLICT waits for the other writer), so a fresh statement in
            # this READ COMMITTED transaction sees it.
            created = (await session.execute(by_name)).scalar()
    if created is None:
        raise RuntimeError(f"anchor alert rule {name!r} vanished after insert conflict")
    return int(created)
