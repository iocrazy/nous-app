"""Worker-process liveness ORM models (Worker Foundation).

  * ``t_worker_registry`` — worker_registry (one row per worker *process*, with
    a boot-generation fencing token for orphan detection)

Distinct from ``agents.AgentWorkers``, which tracks a logical *agent* worker's
state (idle/working/blocked). This table is about OS processes executing any
workload, and is written only by the health sweeper — not user data.

Its own module rather than a corner of ``ops``: ops is at its size ceiling, and
this is a self-contained infra concern.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, Table, Text, Uuid, text

from app.db.orm_base import Base

# worker_registry has NO primary key in the DB — it is a liveness scratchpad
# keyed logically by (executor_id, boot_generation). Mapped as a Table rather
# than a declarative class so no synthetic PK has to be invented.
t_worker_registry = Table(
    "worker_registry",
    Base.metadata,
    Column("executor_id", Text, nullable=False),
    Column(
        "boot_generation",
        Uuid,
        nullable=False,
        comment=(
            "uuid minted once per process boot — fencing token for orphan "
            "detection (P3)."
        ),
    ),
    Column("app_version", Text),
    Column("pid", Integer),
    Column("started_at", DateTime(True), nullable=False, server_default=text("now()")),
    Column(
        "heartbeat_at",
        DateTime(True),
        nullable=False,
        server_default=text("now()"),
        comment=(
            "Last refresh; stale ⇒ that worker process is presumed gone "
            "(observe-only in P1)."
        ),
    ),
    Column("updated_at", DateTime(True), nullable=False, server_default=text("now()")),
    comment=(
        "Per-process worker liveness + boot-generation fencing token. Written by "
        "the health sweeper on the worker; not user data. See Worker Foundation "
        "plan."
    ),
    schema="public",
)
