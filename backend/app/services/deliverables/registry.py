"""产出登记的唯一入口（harness p4 §1-④ / 三期 3a spec §1）。

没登记 = 不存在。四件事一次做完：算版本 → 插行 → 在 run 的
transcript 上落一条 ``deliverable`` 事件 → 把那条事件的 seq 回写到行上。

**顺序不许颠倒**：行先于事件（反过来会出现「有事件没有行」），所以插行那
一刻 seq 还不存在，只能事后补一次 UPDATE——``run_deliverables.seq`` 的唯一
写入方就是 ``_stamp_seq``，补不上只记 WARNING。

**``run_id`` 为空即 no-op**：人手编辑、画布保存、前端直传都会流经同一个
写入点，它们不是 agent 产出，不占版本号，也不该在任何 run 上留事件。
空的写法有三种（``None`` / ``""`` / 测试 sentinel ``"0"``），都算空。

**事件可能落在已经结束的 run 上**：分镜出图走 DBOS，父 run 早已收工。
这条路与 workforce 写 ``subagent_done`` 的完全一样——``RunEventWriter.for_run``。

**kind 错是接线 bug，不是数据问题**：四类之外一律 ``ValueError``。静默跳过
会让那条路的产出永远不存在而没有任何地方说得出来（「触发路径必须类型化
失败回显」）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.repositories.run_deliverables_repository import RunDeliverablesRepository
from app.services.ai.runner.events import emit
from app.services.ai.runner.inbox import clip_claimed_text
from app.services.deliverables.kinds import ALL_KINDS, TITLE_MAX

#: 「没有 run」的三种写法。``scope.run_id`` 在测试路径上是字符串 "0"，
#: 画布路径给空串，其余给 None —— 都不占版本号。
_EMPTY_RUN_IDS = (None, "", 0, "0")


@dataclass(frozen=True)
class DeliverableRow:
    id: str
    run_id: str
    kind: str
    ref_id: str
    version: int
    parent_version: Optional[int]
    title: Optional[str]


async def register_deliverable(
    *,
    run_id: Any,
    kind: str,
    ref_id: Any,
    title: Optional[str] = None,
    model: Optional[str] = None,
    cost_cents: Optional[float] = None,
    turn: Optional[int] = None,
    step: Optional[int] = None,
    recorder: Any = None,
) -> Optional[DeliverableRow]:
    """登记一次产出。``run_id`` 为空返回 ``None`` 且什么都不做。"""
    if run_id in _EMPTY_RUN_IDS:
        return None
    if kind not in ALL_KINDS:
        raise ValueError(f"unknown deliverable kind {kind!r}")
    rid = _bigint_run_id(run_id)
    if rid is None:
        # 不是 BIGINT 的 run id 引用不到 ``agent_runs`` 的任何一行（画布车道
        # 历史上传过 "r1" 这类字符串）。当成「没有 run」，而不是把整次写入
        # 连坐——但说出来，静默会让一条真的接线错误永远不被发现。
        logger.warning(
            f"[deliverables] {kind}/{ref_id}: run_id {run_id!r} is not a bigint "
            "— registered nothing"
        )
        return None

    repo = RunDeliverablesRepository()
    clipped = clip_claimed_text(title or "", TITLE_MAX) or None

    row = await _insert_next_version(
        repo,
        kind=kind,
        ref_id=str(ref_id),
        run_id=rid,
        title=clipped,
        model=model,
        cost_cents=cost_cents,
        turn=turn,
        step=step,
    )

    rec = recorder if recorder is not None else await _writer_for(rid)
    recorded = await emit(
        rec,
        "deliverable",
        {
            "kind": kind,
            "ref_id": str(ref_id),
            "version": row.version,
            "parent_version": row.parent_version,
            "title": clipped,
            "model": model,
            "cost_cents": cost_cents,
            "turn": turn,
            "step": step,
        },
        turn=turn,
        step=step,
    )
    if recorded:
        await _stamp_seq(repo, row, rec)
    return row


async def _stamp_seq(repo: Any, row: DeliverableRow, recorder: Any) -> None:
    """把刚落下的那条事件的 seq 回写到登记行上。

    行先于事件（见 ``register_deliverable`` 的顺序），所以插行时 seq 还不
    存在；``run_deliverables.seq`` 的唯一写入方就是这里。

    **拿不到就不写**：只有真正报得出 seq 的 recorder 才算数。
    ``RunEventWriter.append`` 插失败时返回 ``None`` 而 ``emit`` 仍报 True
    （遥测不连坐一次运行）——那条事件不在 transcript 里，给它编一个 seq 比
    留 NULL 更糟：NULL 说的是「不知道」，错的数字说的是「就在那一步」。

    失败只记 WARNING：行和事件都已经在库里了。
    """
    seq = getattr(recorder, "last_event_seq", None)
    if not isinstance(seq, int) or isinstance(seq, bool):
        return
    try:
        await repo.set_seq(row_id=row.id, seq=seq)
    except Exception as exc:  # noqa: BLE001 — 见 docstring
        logger.warning(
            f"[deliverables] {row.kind}/{row.ref_id} v{row.version}: could not "
            f"stamp seq {seq} onto row {row.id}: {exc!r} — the row and the "
            "event are both intact, only the pointer between them is missing"
        )


async def _insert_next_version(repo: Any, *, kind: str, ref_id: str, **values: Any):
    """SELECT max → INSERT，冲突重算一次。

    第二次一定读得到对方的值（它已提交，正是它让我们撞索引的），
    所以一次重试够用；二次冲突照抛——宁可失败，也不要两个 v2。"""
    from app.db.session import in_unit_of_work

    for attempt in (1, 2):
        previous = await repo.latest_version(kind=kind, ref_id=ref_id)
        try:
            inserted = await repo.insert_version(
                kind=kind,
                ref_id=ref_id,
                version=(previous or 0) + 1,
                parent_version=previous,
                **values,
            )
            return _as_row(inserted)
        except IntegrityError:
            if attempt == 2:
                raise
            if in_unit_of_work():
                # 这次冲突刚把**调用方的**事务弄废了；重算用的 SELECT 会落在
                # 同一个 abort 掉的 session 上抛 PendingRollbackError。那次查询
                # 注定失败，不是值得重试的竞态——照 AssetsRepository.create 与
                # AgentRunInboxRepository 的先例，直接把原始错误交出去。
                raise
            logger.info(
                f"[deliverables] {kind}/{ref_id} v{(previous or 0) + 1} lost the "
                "race — recomputing once"
            )
    raise AssertionError("unreachable")


async def register_deliverable_best_effort(**kwargs: Any) -> Optional[DeliverableRow]:
    """``register_deliverable``，但登记失败不连坐已经成功的那次写入。

    给「产物已经存在」的写入点用：图已经生成并付过钱、分镜卡已经插进库。
    在那之后把整次调用判成失败，agent 会重试，于是同一件东西产两遍——
    比少一行账更糟。

    ⚠️ 不是静默吞错：失败记 ERROR，行的缺席本身也是可查的信号。
    ``ValueError``（kind 写错 = 接线 bug）照抛——那个要在 CI 里炸，不在生产里。
    """
    try:
        return await register_deliverable(**kwargs)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — 见 docstring
        logger.opt(exception=True).error(
            f"[deliverables] registration FAILED for "
            f"{kwargs.get('kind')}/{kwargs.get('ref_id')} on run "
            f"{kwargs.get('run_id')}: {exc!r} — the artifact exists but is "
            "unregistered"
        )
        return None


def _bigint_run_id(run_id: Any) -> Optional[int]:
    try:
        return int(str(run_id))
    except (TypeError, ValueError):
        return None


def _as_row(inserted: dict[str, Any]) -> DeliverableRow:
    return DeliverableRow(
        id=str(inserted.get("id")),
        run_id=str(inserted.get("run_id")),
        kind=str(inserted.get("kind")),
        ref_id=str(inserted.get("ref_id")),
        version=int(inserted.get("version") or 0),
        parent_version=inserted.get("parent_version"),
        title=inserted.get("title"),
    )


class _LateRecorder:
    """``emit`` 只要一个 ``record_event``。把它代理到 run 自己的写入口，
    这样「recorder 没有 record_event 就静默」的语义对两条路都成立。"""

    def __init__(self, writer: Any) -> None:
        self._writer = writer
        #: seq of the last event this recorder actually persisted — ``None``
        #: when the insert failed (``append`` returns None then). Mirrors
        #: ``RunRecorder.last_event_seq`` so the registry reads one name.
        self.last_event_seq: Optional[int] = None

    async def record_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        turn: Optional[int] = None,
        step: Optional[int] = None,
    ) -> None:
        self.last_event_seq = await self._writer.append(
            event_type, payload, turn=turn, step=step
        )


async def _writer_for(run_id: Any) -> Optional[_LateRecorder]:
    """活着的 run 会把自己的 recorder 传进来；走到这里的是 DBOS 侧的
    迟到事件——父 run 早已结束，只能按 seq 续写并折进它**存下来的** views。

    拿不到写入口时返回 ``None``：登记的行已经落库（表是唯一真相），
    少一条 transcript 事件不该把一次成功的产出变成失败。"""
    try:
        from app.services.ai.runner.run_recorder import event_writer_for_run

        return _LateRecorder(await event_writer_for_run(run_id))
    except Exception as exc:  # noqa: BLE001 — telemetry never fails a write
        logger.warning(f"[deliverables] no late writer for run {run_id}: {exc!r}")
        return None


__all__ = [
    "DeliverableRow",
    "register_deliverable",
    "register_deliverable_best_effort",
]
