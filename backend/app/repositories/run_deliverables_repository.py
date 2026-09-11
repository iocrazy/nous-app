"""run_deliverables 的唯一读写口（ORM，无裸 SQL）。

版本号由 ``latest_version`` + 1 得出，并发靠 462 的
``run_deliverables_kind_ref_version_key`` 兜底——所以这里**不**加锁：
一个短事务里的 SELECT-then-INSERT 比 SELECT FOR UPDATE 便宜，
冲突由登记口重算一次（spec §2.1）。

id 一律以 str 出口（Snowflake BIGINT 精度纪律），``cost_cents`` 的
``Decimal`` 转 float——两者都是为了这些行能直接进 JSON。
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, insert, select

from app.db.session import read_scope, write_scope
from app.models.agents import AgentRuns, RunDeliverables
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_DELIVERABLE_N2A = _name_to_attr(RunDeliverables)

#: BIGINT 列：出口一律字符串，JS 侧超过 2^53 会静默丢精度。
_BIGINT_COLS = ("id", "run_id")


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one RunDeliverables row."""
    out = _orm_obj_to_dict(obj, _DELIVERABLE_N2A)
    for key, value in out.items():
        if value is None:
            continue
        if key in _BIGINT_COLS:
            out[key] = str(value)
        elif isinstance(value, _decimal.Decimal):
            out[key] = float(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
    return out


class RunDeliverablesRepository:
    TABLE = "run_deliverables"

    async def latest_version(self, *, kind: str, ref_id: str) -> Optional[int]:
        """当前最高版本号，没有就是 None。走
        ``idx_run_deliverables_ref_latest``（kind, ref_id, version DESC）。"""
        async with read_scope() as session:
            return (
                await session.execute(
                    select(RunDeliverables.version)
                    .where(RunDeliverables.kind == kind)
                    .where(RunDeliverables.ref_id == str(ref_id))
                    .order_by(desc(RunDeliverables.version))
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def insert_version(self, **values: Any) -> Dict[str, Any]:
        """插一行并把它原样返回。撞唯一索引时 ``IntegrityError`` 照抛——
        仲裁在登记口（``_insert_next_version``），不在这里。"""
        async with write_scope() as session:
            row = (
                await session.execute(
                    insert(RunDeliverables).values(**values).returning(RunDeliverables)
                )
            ).scalar_one()
            return _row(row)

    async def list_for_issue(self, issue_id: Any) -> List[Dict[str, Any]]:
        """本 issue 全部产出。issue 归属经 run 反查——
        表上不存 issue_id，``agent_runs.issue_id`` 是唯一真相（spec §3）。"""
        async with read_scope() as session:
            rows = (
                (
                    await session.execute(
                        select(RunDeliverables)
                        .join(AgentRuns, AgentRuns.id == RunDeliverables.run_id)
                        .where(AgentRuns.issue_id == int(issue_id))
                        .order_by(
                            RunDeliverables.kind,
                            RunDeliverables.ref_id,
                            desc(RunDeliverables.version),
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [_row(r) for r in rows]

    async def lineage_for(self, *, kind: str, ref_id: Any) -> List[Dict[str, Any]]:
        """一个对象的整条版本链，新的在前。带上每版所属 issue——
        血缘页要说的正是「这一版是哪次工作产的」。"""
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(RunDeliverables, AgentRuns.issue_id)
                    .join(AgentRuns, AgentRuns.id == RunDeliverables.run_id)
                    .where(RunDeliverables.kind == kind)
                    .where(RunDeliverables.ref_id == str(ref_id))
                    .order_by(desc(RunDeliverables.version))
                )
            ).all()
            return [
                {
                    **_row(row),
                    "issue_id": str(issue_id) if issue_id is not None else None,
                }
                for row, issue_id in rows
            ]


def get_run_deliverables_repository() -> RunDeliverablesRepository:
    return RunDeliverablesRepository()


__all__ = ["RunDeliverablesRepository", "get_run_deliverables_repository"]
