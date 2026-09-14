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
import uuid as _uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, func, insert, select, update

from app.db.session import read_scope, write_scope
from app.models.agents import AgentRuns, RunDeliverables
from app.models.ai import AiAgents
from app.models.reviews import Issues
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_DELIVERABLE_N2A = _name_to_attr(RunDeliverables)

#: BIGINT 列：出口一律字符串，JS 侧超过 2^53 会静默丢精度。
_BIGINT_COLS = ("id", "run_id")


@asynccontextmanager
async def _scope(session: Any, *, write: bool):
    """调用方给了 session 就用它（加入其事务、不 commit）；没给就照旧开自己的。

    回退把「改内容 / 写账本 / 登记版本」放进同一个 postgres 事务——登记在事务外就会
    出现「内容回了、版本没记」（3b spec §2.3 步 4）。"""
    if session is not None:
        yield session
        return
    ctx = write_scope() if write else read_scope()
    async with ctx as owned:
        yield owned


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
        elif isinstance(value, _uuid.UUID):
            # ``actor_user_id`` 是 UUID 列（3b）。asyncpg 交回 ``uuid.UUID``
            # 对象，JSON 编不了——这里转掉，让整行仍然是「可以直接上线的」。
            out[key] = str(value)
    return out


class RunDeliverablesRepository:
    TABLE = "run_deliverables"

    async def latest_version(
        self, *, kind: str, ref_id: str, session: Any = None
    ) -> Optional[int]:
        """当前最高版本号，没有就是 None。走
        ``idx_run_deliverables_ref_latest``（kind, ref_id, version DESC）。"""
        async with _scope(session, write=False) as session:
            return (
                await session.execute(
                    select(RunDeliverables.version)
                    .where(RunDeliverables.kind == kind)
                    .where(RunDeliverables.ref_id == str(ref_id))
                    .order_by(desc(RunDeliverables.version))
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def insert_version(
        self, *, session: Any = None, **values: Any
    ) -> Dict[str, Any]:
        """插一行并把它原样返回。撞唯一索引时 ``IntegrityError`` 照抛——
        仲裁在登记口（``_insert_next_version``），不在这里。

        ``session`` 是关键字**独立**参数，不进 ``values``：它说的是这条语句
        跑在谁的事务里，不是要写进表的一列。"""
        async with _scope(session, write=True) as session:
            row = (
                await session.execute(
                    insert(RunDeliverables).values(**values).returning(RunDeliverables)
                )
            ).scalar_one()
            return _row(row)

    async def set_seq(self, *, row_id: Any, seq: int, session: Any = None) -> None:
        """把 ``deliverable`` 事件的 seq 补回这一行。

        登记口的顺序是「先插行、再落事件」（反过来会出现没有行的事件），
        所以插入那一刻 seq 还不存在，只能事后补一次 UPDATE。调用方把失败
        当成 WARNING 而不是错误——行与事件都已经落库，缺一个定位字段不该
        让一次成功的登记看起来失败了。
        """
        async with _scope(session, write=True) as session:
            await session.execute(
                update(RunDeliverables)
                .where(RunDeliverables.id == int(row_id))
                .values(seq=int(seq))
            )

    async def list_for_issue(self, issue_id: Any) -> List[Dict[str, Any]]:
        """本 issue 产出的每个对象的**整条**链。

        两步，故意分开（3b）：

        1. **哪些对象属于这个 issue** —— 仍然只由 run 决定（内连接 + 唯一真相
           ``agent_runs.issue_id``，表上不存 issue_id，spec §3）。人手版本不
           把一个新对象带进 issue，否则一次回退就能让无关对象出现在别人的
           产出面板上。
        2. **那些对象的哪些版本** —— 全部，包括 ``run_id IS NULL`` 的人手版。
           如果这一步也内连接，第一次回退之后面板上就会缺一版（最新的那版），
           而且不会有任何地方说得出来——正是本仓禁止的 silent no-op（3b spec
           §5 稿二的右栏画的就是 ``v5 ↩ v1``）。
        """
        async with read_scope() as session:
            # 属于这个 issue 的 (kind, ref_id) —— 步 1，run-based。
            belongs = (
                select(RunDeliverables.kind, RunDeliverables.ref_id)
                .join(AgentRuns, AgentRuns.id == RunDeliverables.run_id)
                .where(AgentRuns.issue_id == int(issue_id))
                .distinct()
                .subquery()
            )
            rows = (
                (
                    await session.execute(
                        select(RunDeliverables)
                        .join(
                            belongs,
                            and_(
                                RunDeliverables.kind == belongs.c.kind,
                                RunDeliverables.ref_id == belongs.c.ref_id,
                            ),
                        )
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

        ⚠️ run 侧也是 **outer** join（3b）：回退版的 run_id 是 NULL，内连接会让整条
        链在第一次回退之后当场消失。``list_for_issue`` 的**归属判定**保持内连接
        ——人手版本不属于任何 issue，不该把新对象带进一个 issue 的面板；但那个
        方法列版本时同样不筛 run（见它自己的 docstring）。
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
                    .outerjoin(AgentRuns, AgentRuns.id == RunDeliverables.run_id)
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

    async def get_by_id(
        self, *, row_id: Any, session: Any = None
    ) -> Optional[Dict[str, Any]]:
        """One registered version, by its own id — ``None`` when it is gone.

        Exists for the revert service (3b Task 3), which has to describe the row
        it just inserted **the way the lineage endpoint will**. The insert's
        ``RETURNING`` is narrowed to ``DeliverableRow`` (no ``created_at``, no
        ``seq``), so building the response from it hands the panel a version with
        ``created_at: null`` — a different-looking row for the same version
        depending on which endpoint you asked. Reading back through ``_row``
        gives the one shape both readers share.

        ``session`` joins the caller's transaction, so the read sees the
        not-yet-committed insert it is describing.
        """
        async with _scope(session, write=False) as session:
            row = (
                await session.execute(
                    select(RunDeliverables).where(RunDeliverables.id == int(row_id))
                )
            ).scalar_one_or_none()
        return _row(row) if row is not None else None

    async def output_keys_for_run(self, run_id: Any) -> List[Dict[str, Any]]:
        """这条 run 登记过的 distinct ``(kind, ref_id)``，按首次登记 seq 升序。

        WS 的 ``done`` 帧用它让前端按键精确失效血缘缓存（3b §4）；没登记过就是
        ``[]``。同一个对象被改了几版只出现一次——失效的单位是对象，不是版本。
        ``seq`` 可能是 NULL（登记口先插行后补 seq，见 ``set_seq``），排序按
        ``MIN(seq)``，数据库把 NULL 排在最后，那正是「还没落到 transcript 上」
        该待的位置。
        """
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(
                        RunDeliverables.kind,
                        RunDeliverables.ref_id,
                        func.min(RunDeliverables.seq).label("first_seq"),
                    )
                    .where(RunDeliverables.run_id == int(run_id))
                    .group_by(RunDeliverables.kind, RunDeliverables.ref_id)
                    .order_by(func.min(RunDeliverables.seq))
                )
            ).all()
        return [{"kind": kind, "ref_id": str(ref_id)} for kind, ref_id, _ in rows]

    async def provenance_for(
        self, *, kind: str, ref_ids: List[Any]
    ) -> Dict[str, Dict[str, Any]]:
        """「哪次运行产出了这一件」——按 ref_id 反查，一页一次查询。

        给 Generated 收件箱的来源行用：卡上要印的是 agent 名与 issue 编号，
        所以 run 之外还带出 ``issues.identifier`` 与 ``ai_agents.name``
        （都 outer join —— 没有 issue 的 run 照样要能说出自己是哪个 run）。

        同一对象有多版时取**最新版**的坐标：卡描述的是那张图现在的来历。
        查不到的 ref_id 不出现在返回里；调用方据此退回平文本标签。

        ⚠️ run 侧**刻意仍是内连接**（与 3b 改成 outer 的 ``lineage_for`` 相反）：
        这个方法回答的是「哪次 run 产出了这一件」，没有 run 的行在这个问题下
        无可作答；唯一调用方是 Generated 收件箱，而 ``generated_media`` 今天不
        存在人手版（回退的目标是 script_shot / script_scene）。
        一旦哪天有了，失效方式是**静默给出过期的最新坐标**——最新版是人手版时
        它会被跳过，卡上印的是上一版 agent 版的 run / issue / step，看起来完全
        正常。那时要改的是这里，不是调用方。
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
