"""Revert To vN（三期 3b spec §2.3）。

**回退不是回滚**：以目标版的内容新建 v(latest+1)，血缘记下 ``reverted_from_version``
与 ``actor_user_id``。历史只增不减，所以一次回退本身还能再被回退。

**永不销毁内容**：写回之前先比对当前内容与最新登记版重建出来的内容；不同就说明有
未登记的人手编辑（``PATCH /shots/{id}`` 不写 ops 行），先把当前内容登记成一版并给它
补一行账本（否则那一版以后重建不出来），回退版再占下一个号。一次回退最多两行。

**三步一个事务**：改内容 / 写账本 / 登记版本同事务。登记在事务外意味着先提交内容再
登记，中间失败就留下「内容回了、版本没记」——而那正是血缘要回答的问题。登记口的
``session=`` 参数（Task 2）就是为此存在；场次臂靠 ``write_scope()`` 会 JOIN 环境
``unit_of_work()`` 这一条（``db/session.py``）把仓库那次写也拉进同一个事务。

**只回可回的**：``generated_media`` 重生成写的是**新对象**的 v1（没有旧版可回），
``script_chapter`` 根本没有账本。两者一律 400，不假装成功。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from loguru import logger
from sqlalchemy import insert, select, update

from app.core.scope_guards import verify_scene_access, verify_shot_access
from app.db.session import unit_of_work
from app.models import ScriptOps, ScriptShotOps, ScriptShots
from app.repositories.run_deliverables_repository import (
    get_run_deliverables_repository,
)
from app.services.deliverables.diff import _SHOT_FIELDS, rebuild_content
from app.services.deliverables.lineage_view import version_of
from app.services.deliverables.registry import register_deliverable
from app.services.script.version_service import inverse_between

#: 有账本、因而回得去的两类。其余两类不是「还没做」，是**没有旧版这回事**。
REVERTIBLE_KINDS = ("script_shot", "script_scene")


@dataclass(frozen=True)
class RevertResult:
    """一次回退的产出：新登记的那一版，以及（可选的）回退前被保留下来的那一版。"""

    version: Dict[str, Any]
    kept_version: Optional[Dict[str, Any]]


@dataclass(frozen=True)
class _ChainIdentity:
    """这条链对外的身份：标题 + 它归属的那件工作。

    ``issue_id`` / ``issue_key`` 取自**链上最新的有 run 的那一版**——人手版
    (``run_id IS NULL``) 自己答不出归属，而血缘端点正是这么补的（3b fix 轮 1）。
    两个读者描述同一版必须用同一套值，否则面板在「回退返回的那一版」和「刷新后
    读到的那一版」之间会闪一下 issue。
    """

    title: Optional[str]
    issue_id: Optional[str]
    issue_key: Optional[str]


def _reject(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    """与 ``outputs_router._reject`` 同形：detail 必须是 dict，生产的
    ``ErrorResponse`` 外壳只让 dict 活到 ``details``（CLAUDE.md 2026-09-09）。"""
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message, **extra}
    )


async def visible_chain(kind: str, ref_id: str, auth) -> List[Dict[str, Any]]:
    """与血缘端点同一把可见性尺子。延迟 import 打断 router ↔ service 的环——
    ``registry.py`` 的 ``from app.db.session import in_unit_of_work`` 是同一手法。"""
    from app.api.outputs_router import visible_chain as _chain

    return await _chain(kind, ref_id, auth)


def _chain_identity(rows: List[Dict[str, Any]]) -> _ChainIdentity:
    from app.api.outputs_router import newest_with_a_run

    owner = newest_with_a_run(rows) or {}
    return _ChainIdentity(
        title=rows[0].get("title"),
        issue_id=owner.get("issue_id"),
        issue_key=owner.get("issue_key"),
    )


async def _latest_version(*, kind: str, ref_id: str, session) -> Optional[int]:
    return await get_run_deliverables_repository().latest_version(
        kind=kind, ref_id=ref_id, session=session
    )


async def revert_output(
    *, kind: str, ref_id: str, to_version: int, expected_latest: int, auth
) -> RevertResult:
    """把一个对象回到它的某一旧版。每一次拒绝都是类型化的，没有静默的半成品。"""
    if kind not in REVERTIBLE_KINDS:
        raise _reject(
            status.HTTP_400_BAD_REQUEST,
            "kind_not_revertible",
            f"{kind} has no version chain to revert to (media re-generation "
            "creates a new object; chapters have no ledger)",
        )
    rows = await visible_chain(kind, str(ref_id), auth)  # 404 在里面
    # 写权限走与分镜 PATCH / 场次 ops 完全相同的守卫：能看见 ≠ 能改。
    if kind == "script_shot":
        await verify_shot_access(str(ref_id), auth)
    else:
        await verify_scene_access(str(ref_id), auth)

    latest_row = rows[0]
    latest = int(latest_row["version"])
    by_version = {int(r["version"]): r for r in rows}
    if to_version not in by_version or to_version == latest:
        raise _reject(
            status.HTTP_404_NOT_FOUND,
            "version_not_found",
            f"{kind}/{ref_id} has no earlier version {to_version}",
        )
    if expected_latest != latest:
        raise _reject(
            status.HTTP_409_CONFLICT,
            "version_conflict",
            "someone registered a newer version while you were looking",
            latest_version=latest,
        )

    target, reason = await rebuild_content(kind, str(ref_id), by_version[to_version])
    if target is None:
        raise _reject(
            status.HTTP_409_CONFLICT,
            "content_unavailable",
            f"v{to_version} cannot be rebuilt from the ledger",
            reason=reason,
        )
    head, head_reason = await rebuild_content(kind, str(ref_id), latest_row)
    if head is None:
        raise _reject(
            status.HTTP_409_CONFLICT,
            "content_unavailable",
            f"v{latest} cannot be rebuilt, so 'keep current edits' cannot be decided",
            reason=head_reason,
        )

    chain = _chain_identity(rows)
    uid = str(auth.user_id)
    async with unit_of_work() as session:
        fresh = await _latest_version(kind=kind, ref_id=str(ref_id), session=session)
        if int(fresh or 0) != latest:
            # 两人同时回退同一个对象：后者在事务里才看见对方，仍然 409。
            raise _reject(
                status.HTTP_409_CONFLICT,
                "version_conflict",
                "someone registered a newer version while you were looking",
                latest_version=int(fresh or 0),
            )
        if kind == "script_shot":
            return await _revert_shot(
                ref_id=str(ref_id),
                target=target,
                head=head,
                chain=chain,
                to_version=to_version,
                uid=uid,
                session=session,
            )
        return await _revert_scene(
            ref_id=str(ref_id),
            to_row=by_version[to_version],
            chain=chain,
            to_version=to_version,
            uid=uid,
            session=session,
        )


async def _read_shot_fields(ref_id: str, session) -> Optional[Dict[str, Any]]:
    """锁住这一行再读六个字段——回退与另一次编辑之间没有别的互斥。"""
    row = (
        await session.execute(
            select(*[getattr(ScriptShots, f) for f in _SHOT_FIELDS])
            .where(ScriptShots.id == int(ref_id))
            .with_for_update()
        )
    ).first()
    return None if row is None else {f: getattr(row, f) for f in _SHOT_FIELDS}


async def _write_shot_fields(
    ref_id: str,
    fields: Dict[str, Any],
    *,
    actor: str,
    session,
    before: Optional[Dict[str, Any]],
) -> str:
    """六字段 UPDATE + 一行账本，返回账本行 id（= 新版的 ``ledger_ref``）。

    账本行 ``run_id`` 是 NULL、``actor`` 记人 —— undo 服务按 ``run_id`` 读
    （``run_undo_service.py:62``），所以人手行天然不入 undo，不需要额外过滤。"""
    scene_id = (
        await session.execute(
            select(ScriptShots.scene_id).where(ScriptShots.id == int(ref_id))
        )
    ).scalar_one()
    await session.execute(
        update(ScriptShots).where(ScriptShots.id == int(ref_id)).values(**fields)
    )
    return str(
        (
            await session.execute(
                insert(ScriptShotOps)
                .values(
                    run_id=None,
                    actor=actor,
                    shot_id=int(ref_id),
                    scene_id=scene_id,
                    action="update",
                    before_json=before,
                    after_json=fields,
                )
                .returning(ScriptShotOps.id)
            )
        ).scalar_one()
    )


async def _revert_shot(
    *, ref_id, target, head, chain, to_version, uid, session
) -> RevertResult:
    current = await _read_shot_fields(ref_id, session)
    if current is None:
        raise _reject(
            status.HTTP_404_NOT_FOUND,
            "version_not_found",
            f"script_shot/{ref_id} is gone",
        )
    kept = None
    if current != head:
        kept_ref = await _write_shot_fields(
            ref_id, current, actor=f"keep:{uid}", session=session, before=head
        )
        kept = await _register(
            kind="script_shot",
            ref_id=ref_id,
            chain=chain,
            uid=uid,
            ledger_ref=kept_ref,
            reverted_from=None,
            session=session,
        )
    ledger_ref = await _write_shot_fields(
        ref_id, target, actor=f"revert:{uid}", session=session, before=current
    )
    version = await _register(
        kind="script_shot",
        ref_id=ref_id,
        chain=chain,
        uid=uid,
        ledger_ref=ledger_ref,
        reverted_from=to_version,
        session=session,
    )
    return RevertResult(version=version, kept_version=kept)


async def _revert_scene(
    *, ref_id, to_row, chain, to_version, uid, session
) -> RevertResult:
    """逆操作批，与 undo 服务同机制（``run_undo_service.py:190``）——不造 replace-all op。

    场次账本是完整的（每次元素编辑都写一行 ``script_ops``），所以「未登记的人手编辑」
    在这条臂上不会发生，``kept_version`` 恒为 None。"""
    from app.repositories.script_scene_repository import (
        VersionConflict,
        get_script_scene_repository,
    )

    ledger = [
        {"op_seq": r[0], "op_json": r[1] or {}}
        for r in (
            await session.execute(
                select(ScriptOps.op_seq, ScriptOps.op_json)
                .where(ScriptOps.scene_id == int(ref_id))
                .order_by(ScriptOps.op_seq.asc())
            )
        ).all()
    ]
    current_seq = max((int(r["op_seq"]) for r in ledger), default=0)
    ref = to_row.get("ledger_ref")
    # 存量行没有 ledger_ref：退回「整本账本的末位」，逆操作批为空，回退退化成「把当前
    # 内容登记成新的一版」。宁可少改，也不要按猜出来的水位改写场次。
    from_seq = int(str(ref)) if str(ref or "").isdigit() else current_seq
    inverse = inverse_between(ledger, from_seq=from_seq, to_seq=current_seq)
    try:
        # 逆操作批为空（目标版内容 == 当前）也照走：用户要的是「以 vN 为准」，血缘要
        # 记下这一笔（spec §2.3 步 3b）。apply_ops([]) 只是把 content_version 推一格。
        await get_script_scene_repository().apply_element_ops(
            ref_id, inverse, expected_version=current_seq, actor=f"revert:{uid}"
        )
    except VersionConflict as conflict:
        raise _reject(
            status.HTTP_409_CONFLICT,
            "version_conflict",
            "the scene was edited while the revert was being prepared",
            latest_version=conflict.current_version,
        ) from conflict
    return RevertResult(
        version=await _register(
            kind="script_scene",
            ref_id=ref_id,
            chain=chain,
            uid=uid,
            ledger_ref=str(current_seq + 1),
            reverted_from=to_version,
            session=session,
        ),
        kept_version=None,
    )


async def _register(
    *, kind, ref_id, chain, uid, ledger_ref, reverted_from, session
) -> Dict[str, Any]:
    row = await register_deliverable(
        run_id=None,
        kind=kind,
        ref_id=ref_id,
        title=chain.title,
        actor_user_id=uid,
        reverted_from_version=reverted_from,
        ledger_ref=ledger_ref,
        session=session,
    )
    if row is None:
        # best-effort 在这里是错的：内容已经改了，登记没成 = 血缘说不出这次回退。
        # raise 让整个事务回滚，内容一并退回（spec §2.3 步 4）。
        logger.error(f"[revert] {kind}/{ref_id}: registration returned nothing")
        raise _reject(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "revert_failed",
            "the content was not changed: the new version could not be registered",
        )
    # ``run_id=None`` 走 ``version_of`` 的人手分支：turn / step / deep_link 一律
    # 清空（轨迹坐标是 run 的东西），issue 身份则由这条链补上——与血缘端点同一口径。
    return version_of(
        {
            "id": row.id,
            "version": row.version,
            "parent_version": row.parent_version,
            "run_id": None,
            "issue_id": None,
            "issue_key": None,
            "seq": None,
            "turn": None,
            "step": None,
            "title": row.title,
            "model": None,
            "cost_cents": None,
            "created_at": None,
            "actor_user_id": row.actor_user_id,
            "reverted_from_version": row.reverted_from_version,
        },
        issue_id=chain.issue_id,
        issue_key=chain.issue_key,
    )


__all__ = ["REVERTIBLE_KINDS", "RevertResult", "revert_output", "visible_chain"]
