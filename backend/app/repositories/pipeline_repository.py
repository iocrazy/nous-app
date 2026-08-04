"""Pipeline repository — data access for content relay pipelines (W2b).

SQLAlchemy 2.0 ORM (the issue domain is ORM-only; PostgREST was retired
repo-wide). Snowflake bigint ids are surfaced as STRINGS at the read boundary
(the JS client must never Number() them); UUID columns as strings; timestamps as
native datetimes (Pydantic serializes them). INT columns that are NOT ids
(step_order / current_step) stay native int.

The run-advance / complete / halt / cancel writers are compare-and-swap
UPDATEs keyed on the run's ``current_step`` + ``status`` — that CAS is the
pipeline_relay idempotency substrate (two barrier seams observing the same
terminal edge race to the same UPDATE; exactly one row comes back). See
pipeline_relay.py. Their RETURNING row is deliberately RAW (``.returning(
*IssuePipelineRuns.__table__.columns)`` + ``.mappings().first()`` — ids/uuids
as native int/UUID, NOT run through ``_run_row``'s string coercion) — this
matches the pre-ORM ``execute_returning_one("UPDATE ... RETURNING *")``
behavior byte-for-byte, and is why callers (see pipelines_router.py's
``cancel_run`` handler) re-``get_run()`` when they need the fully
string-coerced read-boundary shape instead of trusting the CAS row directly.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any, Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update

from app.db.session import read_scope, write_scope
from app.models import IssuePipelineRuns, IssuePipelines, IssuePipelineSteps


def _s(v: Any) -> Optional[str]:
    """Coerce a bigint / uuid id to a string (None passes through)."""
    return None if v is None else str(v)


def _step_row(obj: IssuePipelineSteps) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "pipeline_id": str(obj.pipeline_id),
        "step_order": int(obj.step_order),
        "agent_id": str(obj.agent_id),
        "title_template": obj.title_template,
        "prompt_template": obj.prompt_template,
    }


def _pipeline_row(
    obj: IssuePipelines, steps: list[IssuePipelineSteps]
) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "team_id": str(obj.team_id),
        "name": obj.name,
        "description": obj.description,
        "enabled": bool(obj.enabled),
        "created_by_user_id": _s(obj.created_by_user_id),
        "created_at": obj.created_at,
        "updated_at": obj.updated_at,
        "steps": [_step_row(s) for s in sorted(steps, key=lambda s: s.step_order)],
    }


def _run_row(obj: IssuePipelineRuns) -> dict[str, Any]:
    return {
        "id": str(obj.id),
        "pipeline_id": str(obj.pipeline_id),
        "parent_issue_id": str(obj.parent_issue_id),
        "current_step": int(obj.current_step),
        "status": obj.status,
        "halted_reason": obj.halted_reason,
        "started_by_user_id": _s(obj.started_by_user_id),
        "created_at": obj.created_at,
        "updated_at": obj.updated_at,
        "completed_at": obj.completed_at,
    }


class PipelineRepository:
    # ── pipelines ─────────────────────────────────────────────────────────

    async def create_pipeline(
        self,
        *,
        team_id: int,
        name: str,
        description: Optional[str],
        enabled: bool,
        created_by_user_id: Optional[str],
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Insert the pipeline + its ordered steps in one transaction."""
        async with write_scope() as session:
            pipeline = IssuePipelines(
                team_id=int(team_id),
                name=name,
                description=description,
                enabled=enabled,
                created_by_user_id=(
                    _uuid.UUID(created_by_user_id) if created_by_user_id else None
                ),
            )
            session.add(pipeline)
            await session.flush()  # allocate pipeline.id
            step_objs = [
                IssuePipelineSteps(
                    pipeline_id=pipeline.id,
                    step_order=int(s["step_order"]),
                    agent_id=(
                        s["agent_id"]
                        if isinstance(s["agent_id"], _uuid.UUID)
                        else _uuid.UUID(str(s["agent_id"]))
                    ),
                    title_template=s["title_template"],
                    prompt_template=s["prompt_template"],
                )
                for s in steps
            ]
            for so in step_objs:
                session.add(so)
            await session.flush()
            return _pipeline_row(pipeline, step_objs)

    async def get_pipeline(self, pipeline_id: int) -> Optional[dict[str, Any]]:
        async with read_scope() as session:
            pipeline = (
                await session.execute(
                    select(IssuePipelines).where(IssuePipelines.id == int(pipeline_id))
                )
            ).scalar_one_or_none()
            if pipeline is None:
                return None
            steps = (
                (
                    await session.execute(
                        select(IssuePipelineSteps).where(
                            IssuePipelineSteps.pipeline_id == int(pipeline_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            return _pipeline_row(pipeline, list(steps))

    async def list_pipelines(self, team_id: int) -> list[dict[str, Any]]:
        async with read_scope() as session:
            pipelines = (
                (
                    await session.execute(
                        select(IssuePipelines)
                        .where(IssuePipelines.team_id == int(team_id))
                        .order_by(IssuePipelines.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            if not pipelines:
                return []
            ids = [p.id for p in pipelines]
            steps = (
                (
                    await session.execute(
                        select(IssuePipelineSteps).where(
                            IssuePipelineSteps.pipeline_id.in_(ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_pipeline: dict[int, list[IssuePipelineSteps]] = {}
            for s in steps:
                by_pipeline.setdefault(s.pipeline_id, []).append(s)
            return [_pipeline_row(p, by_pipeline.get(p.id, [])) for p in pipelines]

    async def update_pipeline(
        self,
        pipeline_id: int,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
        steps: Optional[list[dict[str, Any]]] = None,
    ) -> Optional[dict[str, Any]]:
        """Patch scalar fields; when ``steps`` is not None, REPLACE the whole
        ordered step set atomically (delete-all + re-insert in one txn)."""
        async with write_scope() as session:
            pipeline = (
                await session.execute(
                    select(IssuePipelines).where(IssuePipelines.id == int(pipeline_id))
                )
            ).scalar_one_or_none()
            if pipeline is None:
                return None
            if name is not None:
                pipeline.name = name
            if description is not None:
                pipeline.description = description
            if enabled is not None:
                pipeline.enabled = enabled
            if steps is not None:
                await session.execute(
                    sa_delete(IssuePipelineSteps).where(
                        IssuePipelineSteps.pipeline_id == int(pipeline_id)
                    )
                )
                for s in steps:
                    session.add(
                        IssuePipelineSteps(
                            pipeline_id=int(pipeline_id),
                            step_order=int(s["step_order"]),
                            agent_id=(
                                s["agent_id"]
                                if isinstance(s["agent_id"], _uuid.UUID)
                                else _uuid.UUID(str(s["agent_id"]))
                            ),
                            title_template=s["title_template"],
                            prompt_template=s["prompt_template"],
                        )
                    )
            await session.flush()
            fresh_steps = (
                (
                    await session.execute(
                        select(IssuePipelineSteps).where(
                            IssuePipelineSteps.pipeline_id == int(pipeline_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            return _pipeline_row(pipeline, list(fresh_steps))

    async def delete_pipeline(self, pipeline_id: int) -> bool:
        async with write_scope() as session:
            result = await session.execute(
                sa_delete(IssuePipelines).where(IssuePipelines.id == int(pipeline_id))
            )
            return result.rowcount > 0

    async def get_step(
        self, pipeline_id: int, step_order: int
    ) -> Optional[dict[str, Any]]:
        async with read_scope() as session:
            obj = (
                await session.execute(
                    select(IssuePipelineSteps).where(
                        IssuePipelineSteps.pipeline_id == int(pipeline_id),
                        IssuePipelineSteps.step_order == int(step_order),
                    )
                )
            ).scalar_one_or_none()
            return _step_row(obj) if obj else None

    async def count_steps(self, pipeline_id: int) -> int:
        async with read_scope() as session:
            steps = (
                (
                    await session.execute(
                        select(IssuePipelineSteps.id).where(
                            IssuePipelineSteps.pipeline_id == int(pipeline_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            return len(steps)

    # ── runs ──────────────────────────────────────────────────────────────

    async def create_run(
        self,
        *,
        pipeline_id: int,
        parent_issue_id: int,
        started_by_user_id: Optional[str],
    ) -> dict[str, Any]:
        async with write_scope() as session:
            run = IssuePipelineRuns(
                pipeline_id=int(pipeline_id),
                parent_issue_id=int(parent_issue_id),
                current_step=1,
                status="running",
                started_by_user_id=(
                    _uuid.UUID(started_by_user_id) if started_by_user_id else None
                ),
            )
            session.add(run)
            await session.flush()
            return _run_row(run)

    async def get_run(self, run_id: int) -> Optional[dict[str, Any]]:
        async with read_scope() as session:
            obj = (
                await session.execute(
                    select(IssuePipelineRuns).where(IssuePipelineRuns.id == int(run_id))
                )
            ).scalar_one_or_none()
            return _run_row(obj) if obj else None

    async def get_active_run_for_parent(
        self, parent_issue_id: int
    ) -> Optional[dict[str, Any]]:
        async with read_scope() as session:
            obj = (
                await session.execute(
                    select(IssuePipelineRuns).where(
                        IssuePipelineRuns.parent_issue_id == int(parent_issue_id),
                        IssuePipelineRuns.status == "running",
                    )
                )
            ).scalar_one_or_none()
            return _run_row(obj) if obj else None

    async def list_runs_for_parent(self, parent_issue_id: int) -> list[dict[str, Any]]:
        async with read_scope() as session:
            objs = (
                (
                    await session.execute(
                        select(IssuePipelineRuns)
                        .where(
                            IssuePipelineRuns.parent_issue_id == int(parent_issue_id)
                        )
                        .order_by(IssuePipelineRuns.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [_run_row(o) for o in objs]

    # ── compare-and-swap state transitions (the idempotency substrate) ────

    async def advance_run_step(
        self, run_id: int, *, from_step: int, to_step: int
    ) -> Optional[dict[str, Any]]:
        """CAS: bump current_step from ``from_step`` to ``to_step`` only while
        the run is still ``running`` AND still on ``from_step``. Returns the new
        row on success (RAW — see module docstring), None if another observer
        already advanced (0 rows)."""
        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        update(IssuePipelineRuns)
                        .where(
                            IssuePipelineRuns.id == int(run_id),
                            IssuePipelineRuns.status == "running",
                            IssuePipelineRuns.current_step == int(from_step),
                        )
                        .values(current_step=int(to_step), updated_at=func.now())
                        .returning(*IssuePipelineRuns.__table__.columns)
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def complete_run(
        self, run_id: int, *, from_step: int
    ) -> Optional[dict[str, Any]]:
        """CAS: mark the run completed only while still running on ``from_step``.
        Returns the row on success (RAW — see module docstring), None if
        already terminal / advanced."""
        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        update(IssuePipelineRuns)
                        .where(
                            IssuePipelineRuns.id == int(run_id),
                            IssuePipelineRuns.status == "running",
                            IssuePipelineRuns.current_step == int(from_step),
                        )
                        .values(
                            status="completed",
                            completed_at=func.now(),
                            updated_at=func.now(),
                        )
                        .returning(*IssuePipelineRuns.__table__.columns)
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def halt_run(self, run_id: int, *, reason: str) -> Optional[dict[str, Any]]:
        """CAS: mark the run halted only while still running. Returns the row
        on success (RAW — see module docstring), None if already terminal."""
        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        update(IssuePipelineRuns)
                        .where(
                            IssuePipelineRuns.id == int(run_id),
                            IssuePipelineRuns.status == "running",
                        )
                        .values(
                            status="halted",
                            halted_reason=reason,
                            completed_at=func.now(),
                            updated_at=func.now(),
                        )
                        .returning(*IssuePipelineRuns.__table__.columns)
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def cancel_run(self, run_id: int) -> Optional[dict[str, Any]]:
        """CAS: user-initiated cancel of a running relay. Returns the row on
        success (RAW — see module docstring), None if already terminal."""
        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        update(IssuePipelineRuns)
                        .where(
                            IssuePipelineRuns.id == int(run_id),
                            IssuePipelineRuns.status == "running",
                        )
                        .values(
                            status="cancelled",
                            completed_at=func.now(),
                            updated_at=func.now(),
                        )
                        .returning(*IssuePipelineRuns.__table__.columns)
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None


pipeline_repository = PipelineRepository()


def get_pipeline_repository() -> PipelineRepository:
    return pipeline_repository
