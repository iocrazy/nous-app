"""产出登记的唯一入口（harness p4 §1-④ / 三期 3a spec §1）。

没登记 = 不存在。四件事一次做完：算版本 → 插行 → 在 run 的
transcript 上落一条 ``deliverable`` 事件 → 把那条事件的 seq 回写到行上。

**顺序不许颠倒**：行先于事件（反过来会出现「有事件没有行」），所以插行那
一刻 seq 还不存在，只能事后补一次 UPDATE——``run_deliverables.seq`` 的唯一
写入方就是 ``_stamp_seq``，补不上只记 WARNING。

**``run_id`` 为空即 no-op**：人手编辑、画布保存、前端直传都会流经同一个
写入点，它们不是 agent 产出，不占版本号，也不该在任何 run 上留事件。
空的写法有三种（``None`` / ``""`` / 测试 sentinel ``"0"``），都算空。
**3b 开了唯一的例外**：显式给了 ``actor_user_id`` 的一次登记（回退）照样
落行——那一行由人署名，没有 run 可挂，所以也不落 transcript 事件。

**没有 recorder 时事件走 ``RunEventWriter.for_run``**：分镜出图走 DBOS，
登记发生在另一个进程/另一个时刻。这条路与 workforce 写 ``subagent_done`` 的
完全一样。⚠️ 那时父 run **不一定已经结束** —— `GenerateShotImage` 只确认
dispatch 就返回，所以两个 writer 同时在一个 run 上是真实情形；见
``_writer_for`` 的 docstring 与 ``RunEventWriter.append`` 的 seq 重试。

**kind 错是接线 bug，不是数据问题**：四类之外一律 ``ValueError``。静默跳过
会让那条路的产出永远不存在而没有任何地方说得出来（「触发路径必须类型化
失败回显」）。

版本语义（undo 不占号 / revert 占、``generated_media`` 恒 v1）见
``services/deliverables/README.md``。
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
    #: ``None`` 的唯一来源是人手登记（3b 回退）——那一行由 ``actor_user_id``
    #: 说出归属，CHECK ``run_deliverables_run_or_actor`` 保证两者必有其一。
    run_id: Optional[str]
    kind: str
    ref_id: str
    version: int
    parent_version: Optional[int]
    title: Optional[str]
    actor_user_id: Optional[str] = None
    reverted_from_version: Optional[int] = None


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
    actor_user_id: Optional[str] = None,
    reverted_from_version: Optional[int] = None,
    ledger_ref: Optional[str] = None,
    session: Any = None,
) -> Optional[DeliverableRow]:
    """登记一次产出。没有 run **也没有** actor 时返回 ``None`` 且什么都不做。

    ``actor_user_id`` 是 3b 开的第二条占号路径，且只开给回退：人手改动依然
    不占版本号（见下面 no-op 分支的注释），只有「某个人把某一版写回去」这件
    事才需要在链上留一行——否则回到的那一版在血缘里无从指认。

    ``session`` 非空时整条链加入调用方的事务（不自开、不 commit）：回退要把
    「改内容 / 写账本 / 登记版本」放进同一个 postgres 事务，登记落在事务外
    就会出现「内容回了、版本没记」（3b spec §2.3 步 4）。
    """
    if reverted_from_version is not None and actor_user_id is None:
        raise ValueError(
            "reverted_from_version needs an actor_user_id — a revert "
            "is a human act and must say whose"
        )
    # kind 先于 no-op 判定（3b fix 轮 1）。合并 no-op 分支之前，非 bigint 的
    # run id（画布道历史上传过 "r1"）会先走到这里并抛——把 kind 排在 no-op
    # 后面会让「run_id='r1' + kind 写错」从 ValueError 退化成 None + WARNING，
    # 即一条真的接线 bug 被降级成一行日志。kind 写错在**每条**路上都是接线
    # bug，与这次登记占不占号无关。
    if kind not in ALL_KINDS:
        raise ValueError(f"unknown deliverable kind {kind!r}")
    rid = None if run_id in _EMPTY_RUN_IDS else _bigint_run_id(run_id)
    if rid is None and actor_user_id is None:
        # 3a 不变量：普通人手改动 / 画布保存 / 前端直传不占号。非 bigint 的 run id
        # 也落这里，但要说出来——静默会让一条真的接线错误永远不被发现。
        if run_id not in _EMPTY_RUN_IDS:
            logger.warning(
                f"[deliverables] {kind}/{ref_id}: run_id {run_id!r} is "
                "not a bigint and no actor was given — registered nothing"
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
        actor_user_id=actor_user_id,
        reverted_from_version=reverted_from_version,
        ledger_ref=ledger_ref,
        session=session,
    )

    if rid is None:
        # 人手版没有 run，也就没有 transcript 可写。血缘端点读的是行，不是事件。
        return row

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
        await _stamp_seq(repo, row, rec, session=session)
    return row


async def _stamp_seq(
    repo: Any, row: DeliverableRow, recorder: Any, *, session: Any = None
) -> None:
    """把刚落下的那条事件的 seq 回写到登记行上。

    行先于事件（见 ``register_deliverable`` 的顺序），所以插行时 seq 还不
    存在；``run_deliverables.seq`` 的唯一写入方就是这里。

    **拿不到就不写**：只有真正报得出 seq 的 recorder 才算数。
    ``RunEventWriter.append`` 插失败时返回 ``None`` 而 ``emit`` 仍报 True
    （遥测不连坐一次运行）——那条事件不在 transcript 里，给它编一个 seq 比
    留 NULL 更糟：NULL 说的是「不知道」，错的数字说的是「就在那一步」。

    **失败的处理跟着同文件 ``_insert_next_version`` 的先例分两支**：

    - 在调用方的事务里（``in_unit_of_work()``）：**照抛**。失败的 UPDATE 刚把
      那个 session 弄废了，吞掉它只是把错误推迟到后面某条无关语句上变成
      ``PendingRollbackError``——把原始错误交出去才说得清发生了什么。
      产物已经存在的写入点用 ``register_deliverable_best_effort``，由它记 ERROR。
    - 不在事务里：只记 WARNING。没有东西被弄废，行和事件都已经在库里，
      少一个定位字段不该把一次成功的登记判成失败。
    """
    from app.db.session import in_unit_of_work

    seq = getattr(recorder, "last_event_seq", None)
    if not isinstance(seq, int) or isinstance(seq, bool):
        return
    try:
        await repo.set_seq(row_id=row.id, seq=seq, session=session)
    except Exception as exc:  # noqa: BLE001 — 见 docstring
        if in_unit_of_work():
            raise
        logger.warning(
            f"[deliverables] {row.kind}/{row.ref_id} v{row.version}: could not "
            f"stamp seq {seq} onto row {row.id}: {exc!r} — the row and the "
            "event are both intact, only the pointer between them is missing"
        )


async def _insert_next_version(
    repo: Any, *, kind: str, ref_id: str, session: Any = None, **values: Any
):
    """SELECT max → INSERT，冲突重算一次。

    第二次一定读得到对方的值（它已提交，正是它让我们撞索引的），
    所以一次重试够用；二次冲突照抛——宁可失败，也不要两个 v2。"""
    from app.db.session import in_unit_of_work

    for attempt in (1, 2):
        previous = await repo.latest_version(kind=kind, ref_id=ref_id, session=session)
        try:
            inserted = await repo.insert_version(
                kind=kind,
                ref_id=ref_id,
                version=(previous or 0) + 1,
                parent_version=previous,
                session=session,
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
    run_id = inserted.get("run_id")
    actor = inserted.get("actor_user_id")
    return DeliverableRow(
        id=str(inserted.get("id")),
        # ``str(None)`` 是 "None" —— 一个看起来像 id 的字符串，比 NULL 更难
        # 发现。人手版的这两个字段各有一个必然为空，所以两边都先判再转。
        run_id=str(run_id) if run_id is not None else None,
        kind=str(inserted.get("kind")),
        ref_id=str(inserted.get("ref_id")),
        version=int(inserted.get("version") or 0),
        parent_version=inserted.get("parent_version"),
        title=inserted.get("title"),
        actor_user_id=str(actor) if actor is not None else None,
        reverted_from_version=inserted.get("reverted_from_version"),
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
    """没有 recorder 可用时的写入口：按 seq 续写并折进那个 run **存下来的**
    views。

    ⚠️ **别读成「父 run 一定已经结束」**（T8c 修复轮 1 更正）。`GenerateShotImage`
    只确认 dispatch 就返回，父 run 继续迭代，所以 DBOS 侧那次
    `register_generated_media`（`workflows/script_shot_generate.py` /
    `script_shot_video.py`）完全可能落在父 run 仍然活着时 —— 同一个 run 上两个
    writer。两半都在 `RunEventWriter` 里兜住了：`append` 撞唯一索引会重新播种
    并重试（谁都不吃掉谁的事件），`refold_external_slices` 在镜像前重折两边都能
    碰的切片，而那次撞车本身就是「有第二个 writer」的通知。

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
