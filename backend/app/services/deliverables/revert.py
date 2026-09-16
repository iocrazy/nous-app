"""Revert To vN（三期 3b spec §2.3）。

**回退不是回滚**：以目标版的内容新建 v(latest+1)，血缘记下 ``reverted_from_version``
与 ``actor_user_id``。历史只增不减，所以一次回退本身还能再被回退。

**永不销毁内容**：写回之前先比对当前内容与最新登记版重建出来的内容；不同就说明这个
对象上有**没被登记成一版**的编辑，先把当前内容登记成一版，回退版再占下一个号。一次
回退最多两行。**两条臂都有这个分支**，区别只在账本：

* 分镜的人手编辑（``PATCH /shots/{id}``）根本不写 ops 行，所以保留版要**补一行账本**
  才重建得出来；⚠️ 也因此这条臂只保得住**账本作证得了的**编辑：一个只被人手动过、
  账本里从没出现过的字段走的是重建的第三档（当前行），比对时判不出差异（见
  ``_revert_shot``）；
* 场次的每一次编辑都经 ``apply_element_ops`` 写了 ``script_ops``，账本是全的——缺的
  只是「哪一版看到哪个水位」，所以保留版**不写新账本**，只把当前 ``max(op_seq)``
  记成它的 ``ledger_ref``。

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
from sqlalchemy.exc import IntegrityError

from app.core.scope_guards import verify_scene_access, verify_shot_access
from app.db.session import unit_of_work
from app.models import ScriptOps, ScriptShotOps, ScriptShots
from app.repositories.run_deliverables_repository import (
    get_run_deliverables_repository,
)
from app.services.deliverables.diff import (
    _SHOT_FIELDS,
    rebuild_content,
    render_elements,
    render_shot,
)
from app.services.deliverables.lineage_view import version_of
from app.services.deliverables.registry import register_deliverable
from app.services.script.version_service import inverse_between, replay_to

#: Postgres 的唯一性冲突。``run_deliverables_kind_ref_version_key`` 是两次并发回退
#: 的**唯一**仲裁者（登记口按 SELECT max → INSERT 取号，两个请求可以读到同一个
#: max），所以这个码要能被单独认出来：别的 ``IntegrityError``（CHECK、外键）是真
#: 缺陷，把它们也说成「你慢了一步」等于用一个安抚性的 409 盖掉一个 bug。
_UNIQUE_VIOLATION = "23505"

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
    """与血缘端点同一把可见性尺子。

    3c 起这把尺子在服务层有了自己的名字（``deliverables/visibility.py``），因为
    引用解析成了第三个消费方。这里保留本地名是为了既有的桩点（测试按
    ``revert.visible_chain`` 打桩），转调过去而不是再抄一次延迟 import。"""
    from app.services.deliverables.visibility import assert_chain_visible

    return await assert_chain_visible(kind, ref_id, auth)


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


async def _stored_version_row(*, row_id, session) -> Optional[Dict[str, Any]]:
    """刚登记的那一行，按血缘端点读它的样子读回来（同一个事务，看得见未提交的插入）。

    登记口的 ``RETURNING`` 窄化成 ``DeliverableRow``，没有 ``created_at`` / ``seq``；
    照它组装响应等于对**同一版**给出两种描述——回退接口说 ``created_at: null``，
    刷新后血缘接口说一个时间戳。多读一行换两个读者说同一句话。"""
    return await get_run_deliverables_repository().get_by_id(
        row_id=row_id, session=session
    )


async def _assert_may_write(kind: str, ref_id: str, auth) -> None:
    """写权限：**能看见 ≠ 能改**。

    走的就是分镜 PATCH（``script_shots_router.py:134``）与场次 ops
    （``script_scenes_router.py:236``）用的那一对守卫——同一个对象上两条写路径用
    两套权限判断，迟早会分叉，而回退这条是后来的那一条。

    **但要把它们的拒绝翻译成类型化的。** 两个守卫抛的是裸字符串 detail（"Access
    denied" / "Shot not found"，``scope_guards.py:254/336/371``），而生产的
    ``ErrorResponse`` 外壳只让 **dict** detail 活到 ``details``（CLAUDE.md
    2026-09-09）。不翻译的话，这个端点上**唯一**一个没有类型化码的拒绝恰好就是
    「不许你改」——前端只拿得到 ``code: http_403`` 与 "403 Forbidden"，而它旁边每一种
    拒绝都带着码。状态码原样保留，守卫自己的文案原样转述进 ``message``。
    """
    guard = verify_shot_access if kind == "script_shot" else verify_scene_access
    try:
        await guard(str(ref_id), auth)
    except HTTPException as denied:
        if isinstance(denied.detail, dict):
            # 已经是类型化的（将来守卫自己改好了）——别再包一层把码埋进去。
            raise
        raise _reject(
            denied.status_code,
            # 今天这两个守卫只产 403 / 404 两种；再出现别的状态码时按「不是 403
            # 就是找不到」归类会说错话，所以这里的映射要跟着守卫一起 review。
            (
                "not_permitted"
                if denied.status_code == status.HTTP_403_FORBIDDEN
                else "not_found"
            ),
            str(denied.detail),
        ) from denied


def _is_unique_violation(exc: IntegrityError) -> bool:
    """这次 ``IntegrityError`` 是不是唯一索引冲突（SQLSTATE 23505）。

    驱动之间键名不同：asyncpg 的异常带 ``sqlstate``，psycopg 带 ``pgcode``。两个都
    读，都没有就当**不是**——宁可让一个认不出来的完整性错误以 500 + 栈暴露出来，
    也不要把它伪装成一次可重试的竞态。"""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return str(code) == _UNIQUE_VIOLATION


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
    await _assert_may_write(kind, str(ref_id), auth)

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
    try:
        async with unit_of_work() as session:
            fresh = await _latest_version(
                kind=kind, ref_id=str(ref_id), session=session
            )
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
                # 回退之后这一场的内容就是它（逆操作批就是照它算的）。传进去
                # 只为渲染检索正文，不参与任何写入决策。
                target=target,
                head=head,
                chain=chain,
                to_version=to_version,
                uid=uid,
                session=session,
            )
    except IntegrityError as conflict:
        # 上面那次 `_latest_version` 复查不加锁，所以两个并发回退可以双双通过它，
        # 由唯一索引最后仲裁。**这个 except 必须在 `async with` 外面**：事务要先
        # 因为异常穿出而回滚（`session.begin()` 的 `__aexit__` 干这件事），内容才
        # 真的退了回去；在里面接住的话 session 已经 abort，随后每一条语句都会变成
        # `PendingRollbackError`，而我们还需要再读一次库。
        if not _is_unique_violation(conflict):
            # CHECK / 外键违例是真缺陷，不是竞态。原样交出去变成 500 并带栈——
            # 把它说成 409「你慢了一步」等于用一句安抚盖掉一个 bug。
            raise
        latest_now = await _latest_version(kind=kind, ref_id=str(ref_id), session=None)
        logger.info(
            f"[revert] {kind}/{ref_id}: lost the version race at v{latest + 1} "
            f"— latest is now v{latest_now}; the content was rolled back"
        )
        raise _reject(
            status.HTTP_409_CONFLICT,
            "version_conflict",
            "someone registered a newer version while you were looking",
            latest_version=int(latest_now or 0),
        ) from conflict


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
    # ``head`` = 最新登记版按账本重建出来的内容（``diff.rebuild_content``，三档）。
    # 不同 ⇒ 水位之后有**账本看得见的**编辑没被登记，先把当前状态登记成一版。
    #
    # ⚠️ 这条臂只保得住账本作证得了的编辑。``PATCH /shots/{id}`` 不写 ops 行，所以
    # 一个只被人手改过、账本里从没出现过的字段会走 ``rebuild_content`` 的第三档、
    # 读出**当前**值 —— 于是 ``head`` 里已经有了它，这里判不出差异，那次人手编辑
    # 也就没有被单独登记成一版（它没有被销毁：回退写回的同样是那个当前值）。
    # 根治要让 ``PATCH /shots`` 也写一行账本（actor 记人），那是单独一票。
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
            search_text=render_shot(current),
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
        search_text=render_shot(target),
        session=session,
    )
    return RevertResult(version=version, kept_version=kept)


async def _revert_scene(
    *, ref_id, to_row, target, head, chain, to_version, uid, session
) -> RevertResult:
    """逆操作批，与 undo 服务同机制（``run_undo_service.py:190``）——不造 replace-all op。

    结构与 ``_revert_shot`` 一致：先读当前内容、与最新登记版比、不同就保留一版，
    再改内容、再登记。**差别只在保留版怎么变得可重建**：分镜要补一行账本（人手 PATCH
    不写 ops），场次的账本本来就是全的（每次编辑都过 ``apply_element_ops``），缺的只是
    「这一版看到哪个水位」，所以它的 ``ledger_ref`` 就是当前的 ``max(op_seq)``，一行
    新账本都不用写。

    场次**确实**会出现「未登记的人手编辑」：编辑器里改一句台词写了 ``script_ops``，
    但不占版本号（登记只开给 agent 产出与回退）。不保留它，一次回退就会把用户刚写的
    那段悄悄冲掉且再也指认不出来。"""
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

    kept = None
    # 当前内容 = 整本账本重放到末位；``head`` = 最新登记版重建出来的内容（按它自己的
    # ``ledger_ref`` 水位切）。两者不同 ⇒ 水位之后有没被登记的编辑。
    #
    # ⚠️ 存量的最新登记行没有 ``ledger_ref``（3b 之前登记的都没有），那时
    # ``diff._prefix_for``（``diff.py:116-124``）退回按 ``created_at`` 切账本——
    # 那个时间戳只是**巧合**单调的：同一个事务里的两次写共享事务开始时间。切偏一行
    # 就会让 ``head`` 少一笔，于是这里判定「有未登记的编辑」并**多登记一版**。
    # 后果是良性的（多留一版历史，没有内容被销毁，方向与「永不销毁内容」一致），
    # 但那一版是假的。这类链会随着每次回退自己补上 ``ledger_ref`` 而消失。
    current_elements = replay_to(ledger, current_seq)
    if current_elements != head:
        kept = await _register(
            kind="script_scene",
            ref_id=ref_id,
            chain=chain,
            uid=uid,
            ledger_ref=str(current_seq),
            reverted_from=None,
            search_text=render_elements(current_elements),
            session=session,
        )

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
            search_text=render_elements(target),
            session=session,
        ),
        kept_version=kept,
    )


async def _register(
    *, kind, ref_id, chain, uid, ledger_ref, reverted_from, search_text, session
) -> Dict[str, Any]:
    row = await register_deliverable(
        run_id=None,
        kind=kind,
        ref_id=ref_id,
        title=chain.title,
        actor_user_id=uid,
        reverted_from_version=reverted_from,
        ledger_ref=ledger_ref,
        # 这一版的内容回退已经算出来了（保留版 = 当前内容，回退版 = 目标内容），
        # 所以正文不用再重建一次。缺了它，人手版在检索里只有标题——而链上所有
        # 标题都一样（``chain.title``），等于搜不到。
        search_text=search_text,
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
    # 回读刚插的那一行（同一个事务），拿服务器才有的 ``created_at``。登记口的
    # ``RETURNING`` 窄化成 ``DeliverableRow``，照它组装等于对**同一版**给出两种
    # 描述：回退接口说 ``created_at: null``，刷新后血缘接口说一个时间戳。
    stored = await _stored_version_row(row_id=row.id, session=session)
    if stored is None:
        # 行刚插进去、读不回来：不该发生，但它只影响这次响应的装饰字段，
        # 内容与登记都已经成立。为此把一次成功的回退判成失败是更坏的交易。
        logger.warning(
            f"[revert] {kind}/{ref_id} v{row.version}: could not read the row "
            "back — the response loses created_at / seq, nothing else"
        )
        stored = {
            "id": row.id,
            "version": row.version,
            "parent_version": row.parent_version,
            "title": row.title,
            "actor_user_id": row.actor_user_id,
            "reverted_from_version": row.reverted_from_version,
        }
    # ``run_id=None`` 走 ``version_of`` 的人手分支：turn / step / deep_link 一律
    # 清空（轨迹坐标是 run 的东西），issue 身份则由这条链补上——与血缘端点同一口径。
    # ``stored`` 是仓库 ``_row`` 出来的，键与 ``lineage_for`` 的行完全一致，所以
    # ``version_of`` 的投影对两个读者做的是同一件事。
    return version_of(
        {**stored, "run_id": None, "issue_id": None, "issue_key": None},
        issue_id=chain.issue_id,
        issue_key=chain.issue_key,
    )


__all__ = ["REVERTIBLE_KINDS", "RevertResult", "revert_output", "visible_chain"]
