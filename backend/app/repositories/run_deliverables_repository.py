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

from sqlalchemy import desc, insert, select, update

from app.db.session import read_scope, write_scope
from app.models.agents import AgentRuns, RunDeliverables
from app.models.ai import AiAgents
from app.models.reviews import Issues
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

    async def set_seq(self, *, row_id: Any, seq: int) -> None:
        """把 ``deliverable`` 事件的 seq 补回这一行。

        登记口的顺序是「先插行、再落事件」（反过来会出现没有行的事件），
        所以插入那一刻 seq 还不存在，只能事后补一次 UPDATE。调用方把失败
        当成 WARNING 而不是错误——行与事件都已经落库，缺一个定位字段不该
        让一次成功的登记看起来失败了。
        """
        async with write_scope() as session:
            await session.execute(
                update(RunDeliverables)
                .where(RunDeliverables.id == int(row_id))
                .values(seq=int(seq))
            )

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
        血缘页要说的正是「这一版是哪次工作产的」。

        issue 侧是 **outer** join：没有 issue 的 run（画布 / 聊天道）照样有
        产出，内连接会让它们整条链消失。逐行连是必须的——同一个对象的两版
        可以出自两个 issue 的 run（见集成测试 case 6）。

        ``issue_key`` / ``team_id`` 是给 deep link 用的（3a Task 3b）：
        前端 issue 路由按 ``MH-n`` + team 寻址，``issue_id`` 拼不出可用 URL。
        ``team_id`` 到 ``lineage_view`` 为止，不上线。
        """
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(
                        RunDeliverables,
                        AgentRuns.issue_id,
                        Issues.identifier,
                        Issues.team_id,
                    )
                    .join(AgentRuns, AgentRuns.id == RunDeliverables.run_id)
                    .outerjoin(Issues, Issues.id == AgentRuns.issue_id)
                    .where(RunDeliverables.kind == kind)
                    .where(RunDeliverables.ref_id == str(ref_id))
                    .order_by(desc(RunDeliverables.version))
                )
            ).all()
            return [
                {
                    **_row(row),
                    "issue_id": str(issue_id) if issue_id is not None else None,
                    "issue_key": identifier,
                    "team_id": str(team_id) if team_id is not None else None,
                }
                for row, issue_id, identifier, team_id in rows
            ]

    async def provenance_for(
        self, *, kind: str, ref_ids: List[Any]
    ) -> Dict[str, Dict[str, Any]]:
        """「哪次运行产出了这一件」——按 ref_id 反查，一页一次查询。

        给 Generated 收件箱的来源行用：卡上要印的是 agent 名与 issue 编号，
        所以 run 之外还带出 ``issues.identifier`` 与 ``ai_agents.name``
        （都 outer join —— 没有 issue 的 run 照样要能说出自己是哪个 run）。

        同一对象有多版时取**最新版**的坐标：卡描述的是那张图现在的来历。
        查不到的 ref_id 不出现在返回里；调用方据此退回平文本标签。
        """
        wanted = [str(r) for r in ref_ids if r is not None]
        if not wanted:
            return {}
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(
                        RunDeliverables.ref_id,
                        RunDeliverables.run_id,
                        RunDeliverables.version,
                        RunDeliverables.step,
                        RunDeliverables.turn,
                        AgentRuns.issue_id,
                        Issues.identifier,
                        AiAgents.name,
                    )
                    .join(AgentRuns, AgentRuns.id == RunDeliverables.run_id)
                    .outerjoin(Issues, Issues.id == AgentRuns.issue_id)
                    .outerjoin(AiAgents, AiAgents.id == AgentRuns.agent_id)
                    .where(RunDeliverables.kind == kind)
                    .where(RunDeliverables.ref_id.in_(wanted))
                    .order_by(RunDeliverables.ref_id, RunDeliverables.version)
                )
            ).all()
        # ORDER BY version ASC + 覆盖写 = 每个 ref_id 留下最新版。
        out: Dict[str, Dict[str, Any]] = {}
        for ref_id, run_id, _version, step, turn, issue_id, identifier, name in rows:
            out[str(ref_id)] = {
                "run_id": str(run_id),
                "issue_id": str(issue_id) if issue_id is not None else None,
                "issue_key": identifier,
                "agent_name": name,
                "step": step,
                "turn": turn,
            }
        return out


def get_run_deliverables_repository() -> RunDeliverablesRepository:
    return RunDeliverablesRepository()


__all__ = ["RunDeliverablesRepository", "get_run_deliverables_repository"]
