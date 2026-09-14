"""Two versions of one产出, as content the panel can draw (三期 3a spec §4).

``run_deliverables`` records THAT a version happened; it does not store the
version's bytes. So each kind is reconstructed from the ledger that already
exists for it:

* ``script_shot`` — fold ``script_shot_ops.after_json`` (create carries every
  writable field, update carries only the ones it changed) up to the ops row
  the deliverable's ``ledger_ref`` names, or, for rows registered before 3b,
  the newest one at or before its ``created_at`` (see ``_prefix_for``).
* ``script_scene`` — replay ``script_ops`` through the pure
  ``version_service.replay_to`` to the same watermark.
* ``generated_media`` — there is nothing to fold: a regeneration writes a NEW
  row with a NEW id, so it registers as v1 of a NEW object. Both sides of a
  media diff therefore describe the same row today. The shape is here so the
  panel has one contract, not because media revisions exist yet.
* ``script_chapter`` — no per-object ledger, and no producer registers one
  today either (spec §0). Both sides come back unavailable.

**A side that cannot be reconstructed says so.** ``available=False`` plus a
typed ``unavailable_reason`` beats an empty string, which the panel would draw
as "this version was blank" — a different and wrong claim.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import select

from app.db.session import read_scope
from app.models import GeneratedMedia, ScriptOps, ScriptShotOps
from app.services.script.version_service import replay_to

#: The shot fields a diff prints, in a stable order (mirrors the gateway's
#: ``_WRITABLE_SHOT_FIELDS``; order is the reading order, not the column order).
_SHOT_FIELDS: Tuple[str, ...] = (
    "shot_type",
    "camera_angle",
    "camera_movement",
    "focal_length",
    "lighting",
    "description",
)

#: Why a side has no text. Typed so the panel can say something specific.
NO_SNAPSHOT = "no_snapshot"
NO_LEDGER = "no_ledger"
NOT_FOUND = "not_found"


# ── pure rendering ───────────────────────────────────────────────────────


def render_shot(fields: Dict[str, Any]) -> str:
    """A shot snapshot as one ``key: value`` line per filled field."""
    lines = [
        f"{key}: {fields[key]}"
        for key in _SHOT_FIELDS
        if fields.get(key) not in (None, "")
    ]
    return "\n".join(lines)


def render_elements(elements: List[Dict[str, Any]]) -> str:
    """A scene's element array as one ``type: text`` line per element.

    Deliberately not screenplay formatting: the two panes are compared line by
    line, and a renderer that indents dialogue differently from action would
    make a moved element look like an edited one.
    """
    lines = []
    for element in elements or []:
        text = str(element.get("text") or "").strip()
        lines.append(f"{element.get('type') or 'action'}: {text}")
    return "\n".join(lines)


def _as_datetime(value: Any) -> Optional[_dt.datetime]:
    """ISO string or datetime → aware datetime. Naive values are read as UTC so
    a comparison against a ``timestamptz`` column never raises."""
    if isinstance(value, _dt.datetime):
        stamp = value
    elif isinstance(value, str) and value:
        try:
            stamp = _dt.datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_dt.timezone.utc)
    return stamp


def _prefix_for(
    ledger: List[Tuple[Any, Optional[_dt.datetime], int]], row: Dict[str, Any]
) -> List[Any]:
    """这一版能看见的账本前缀。

    优先 ``ledger_ref``（登记时记下的账本位置：shot 是 script_shot_ops.id，scene 是
    script_ops.op_seq 水位）——那是**外键**；``created_at`` 只是巧合上单调的时间戳，
    一个事务里的两次写共享事务开始时间，按它切会把 v1 画成 v2。存量行没有
    ledger_ref 且永远不会有，所以时间戳这条退路是常设的，不是过渡期妥协。解析不出
    数字的脏值同样退回时间戳：一条血缘不该因为一个字段而 500。"""
    ref = row.get("ledger_ref")
    if ref not in (None, ""):
        try:
            cut = int(str(ref))
        except (TypeError, ValueError):
            cut = None
        if cut is not None:
            return [payload for payload, _created, key in ledger if key <= cut]
    stamp = _as_datetime(row.get("created_at"))
    if stamp is None:
        return [payload for payload, _created, _key in ledger]
    return [
        payload
        for payload, created, _key in ledger
        if created is not None and created <= stamp
    ]


# ── per-kind reconstruction ──────────────────────────────────────────────


async def _shot_ledger(
    ref_id: str,
) -> List[Tuple[Dict[str, Any], Optional[_dt.datetime], int]]:
    """``(after_json, created_at, id)`` per ops row, oldest first.

    Ordered by ``id`` (snowflake, monotonic) rather than ``created_at``: the id
    IS ``ledger_ref``'s domain, so the ordering and the cut key are the same
    thing.

    A ``created_at`` of NULL no longer drops the row HERE — it used to vanish
    from every reconstruction without a word. It still cannot be seen by the
    timestamp FALLBACK in ``_prefix_for`` (that branch has nothing to compare
    against), so a version registered before 3b still reconstructs without it;
    a version carrying a ``ledger_ref`` sees it.
    """
    async with read_scope() as session:
        rows = (
            await session.execute(
                select(
                    ScriptShotOps.after_json,
                    ScriptShotOps.created_at,
                    ScriptShotOps.id,
                )
                .where(ScriptShotOps.shot_id == int(ref_id))
                .order_by(ScriptShotOps.id.asc())
            )
        ).all()
    return [(row[0] or {}, _as_datetime(row[1]), int(row[2])) for row in rows]


async def _scene_ledger(
    ref_id: str,
) -> List[Tuple[Dict[str, Any], Optional[_dt.datetime], int]]:
    """``({op_seq, op_json}, created_at, op_seq)`` per ops row, oldest first.
    ``op_seq`` is both the replay watermark and this kind's ``ledger_ref``."""
    async with read_scope() as session:
        rows = (
            await session.execute(
                select(ScriptOps.op_seq, ScriptOps.op_json, ScriptOps.created_at)
                .where(ScriptOps.scene_id == int(ref_id))
                .order_by(ScriptOps.op_seq.asc())
            )
        ).all()
    return [
        (
            {"op_seq": row[0], "op_json": row[1] or {}},
            _as_datetime(row[2]),
            int(row[0]),
        )
        for row in rows
    ]


async def _media_row(ref_id: str) -> Optional[Dict[str, Any]]:
    async with read_scope() as session:
        row = (
            await session.execute(
                select(
                    GeneratedMedia.id,
                    GeneratedMedia.media_kind,
                    GeneratedMedia.mime,
                ).where(GeneratedMedia.id == int(ref_id))
            )
        ).first()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "media_kind": row[1],
        "mime": row[2],
        "cover_url": f"/api/v1/generated-media/{row[0]}/cover",
        "stream_url": f"/api/v1/generated-media/{row[0]}/stream",
    }


def _side(row: Dict[str, Any], **content: Any) -> Dict[str, Any]:
    """The metadata half of a side — identical for every kind."""
    return {
        "version": row.get("version"),
        "run_id": row.get("run_id"),
        "issue_id": row.get("issue_id"),
        "created_at": row.get("created_at"),
        "model": row.get("model"),
        "cost_cents": row.get("cost_cents"),
        "title": row.get("title"),
        "text": None,
        "media": None,
        "available": False,
        "unavailable_reason": None,
        **content,
    }


def _unavailable(row: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return _side(row, available=False, unavailable_reason=reason)


async def build_diff(
    *, kind: str, ref_id: str, from_row: Dict[str, Any], to_row: Dict[str, Any]
) -> Dict[str, Any]:
    """Both sides of a diff between two registered versions of one object.

    Never raises for missing content: a ledger this reader cannot reconstruct
    yields two unavailable sides, because the versions themselves are real —
    the registry says so — and a 500 here would hide that.
    """
    content_type = "media" if kind == "generated_media" else "text"
    try:
        sides = await _sides(kind, str(ref_id), from_row, to_row)
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.opt(exception=True).error(
            f"[deliverables] diff {kind}/{ref_id} could not be reconstructed: {exc!r}"
        )
        sides = (_unavailable(from_row, NOT_FOUND), _unavailable(to_row, NOT_FOUND))
    return {
        "kind": kind,
        "ref_id": str(ref_id),
        "content_type": content_type,
        "from": sides[0],
        "to": sides[1],
    }


async def _sides(
    kind: str, ref_id: str, from_row: Dict[str, Any], to_row: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if kind == "generated_media":
        media = await _media_row(ref_id)
        if media is None:
            return _unavailable(from_row, NOT_FOUND), _unavailable(to_row, NOT_FOUND)
        return (
            _side(from_row, media=media, available=True),
            _side(to_row, media=media, available=True),
        )

    if kind == "script_shot":
        ledger = await _shot_ledger(ref_id)
        return tuple(  # type: ignore[return-value]
            _shot_side(row, ledger) for row in (from_row, to_row)
        )

    if kind == "script_scene":
        ledger = await _scene_ledger(ref_id)
        return tuple(  # type: ignore[return-value]
            _scene_side(row, ledger) for row in (from_row, to_row)
        )

    # script_chapter: registered by nobody today and backed by no per-object
    # ledger. Saying so beats inventing a reconstruction.
    return _unavailable(from_row, NO_LEDGER), _unavailable(to_row, NO_LEDGER)


def _fold_shot(prefix: List[Any]) -> Dict[str, Any]:
    """一段分镜账本前缀 → 那一刻的六字段快照。

    ``create`` 带齐每个可写字段，``update`` 只带它改过的，所以折叠就是按序
    ``update``。缺席的字段读成 ``None`` —— 那正是列的实际状态。"""
    snapshot: Dict[str, Any] = {}
    for after in prefix:
        snapshot.update(after or {})
    return {field: snapshot.get(field) for field in _SHOT_FIELDS}


def _replay_scene(prefix: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """一段场次账本前缀 → 那一刻的元素数组。

    水位就是前缀的末尾。``ledger_ref`` 已经在 ``_prefix_for`` 里切过一刀，
    这里再按它算一次是同一个界的第二份算法：两处冗余意味着拆掉任何一处都
    没有测试会红（3b fix 轮 1 实测）。一个口径，一个可证伪点。"""
    return replay_to(prefix, max(int(op["op_seq"]) for op in prefix))


def _shot_side(
    row: Dict[str, Any], ledger: List[Tuple[Any, Any, int]]
) -> Dict[str, Any]:
    prefix = _prefix_for(ledger, row)
    if not prefix:
        return _unavailable(row, NO_SNAPSHOT)
    return _side(row, text=render_shot(_fold_shot(prefix)), available=True)


def _scene_side(
    row: Dict[str, Any], ledger: List[Tuple[Any, Any, int]]
) -> Dict[str, Any]:
    prefix = _prefix_for(ledger, row)
    if not prefix:
        return _unavailable(row, NO_SNAPSHOT)
    return _side(row, text=render_elements(_replay_scene(prefix)), available=True)


async def rebuild_content(
    kind: str, ref_id: str, row: Dict[str, Any]
) -> Tuple[Any, Optional[str]]:
    """一版的**内容**（不是渲染后的文本）：shot 是六字段 dict，scene 是元素数组。

    回退写回的就是它，所以这里和 ``_shot_side`` / ``_scene_side`` 走的是同一条
    折叠路径（``_fold_shot`` / ``_replay_scene``）——两套重建等于两种「v1 是什么」
    的说法，而回退会把其中一种当真写进库。``(None, reason)`` 表示重建不出来，
    调用方据此 409 而不是把一个空内容写回去。

    ``generated_media`` / ``script_chapter`` 落到最后一行的 ``NO_LEDGER``：媒体
    重生成是**新对象**的 v1（没有可回的旧版），章节根本没有账本。
    """
    try:
        if kind == "script_shot":
            prefix = _prefix_for(await _shot_ledger(ref_id), row)
            if not prefix:
                return None, NO_SNAPSHOT
            return _fold_shot(prefix), None
        if kind == "script_scene":
            prefix = _prefix_for(await _scene_ledger(ref_id), row)
            if not prefix:
                return None, NO_SNAPSHOT
            return _replay_scene(prefix), None
    except Exception as exc:  # noqa: BLE001 — 同 build_diff：说不出来也要说出来
        logger.opt(exception=True).error(
            f"[deliverables] rebuild {kind}/{ref_id} v{row.get('version')} "
            f"failed: {exc!r}"
        )
        return None, NOT_FOUND
    return None, NO_LEDGER


__all__ = [
    "NO_LEDGER",
    "NO_SNAPSHOT",
    "NOT_FOUND",
    "build_diff",
    "rebuild_content",
    "render_elements",
    "render_shot",
]
