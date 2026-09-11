"""Two versions of one产出, as content the panel can draw (三期 3a spec §4).

``run_deliverables`` records THAT a version happened; it does not store the
version's bytes. So each kind is reconstructed from the ledger that already
exists for it:

* ``script_shot`` — fold ``script_shot_ops.after_json`` (create carries every
  writable field, update carries only the ones it changed) up to the ops row
  that is the newest at or before the deliverable's ``created_at``.
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


def _at_or_before(ledger: List[Tuple[Any, _dt.datetime]], when: Any) -> List[Any]:
    """The ledger prefix a version could have seen.

    ``when`` is the deliverable's ``created_at``; the registration happens right
    after the write commits, so every ledger row at or before it belongs to that
    version. A version with no usable timestamp takes the WHOLE ledger rather
    than none: showing the latest known content is a smaller error than claiming
    the version had no content.
    """
    stamp = _as_datetime(when)
    if stamp is None:
        return [payload for payload, _ in ledger]
    return [payload for payload, created in ledger if created <= stamp]


# ── per-kind reconstruction ──────────────────────────────────────────────


async def _shot_ledger(ref_id: str) -> List[Tuple[Dict[str, Any], _dt.datetime]]:
    async with read_scope() as session:
        rows = (
            await session.execute(
                select(ScriptShotOps.after_json, ScriptShotOps.created_at)
                .where(ScriptShotOps.shot_id == int(ref_id))
                .order_by(ScriptShotOps.created_at.asc(), ScriptShotOps.id.asc())
            )
        ).all()
    return [(row[0] or {}, _as_datetime(row[1])) for row in rows if row[1] is not None]


async def _scene_ledger(ref_id: str) -> List[Tuple[Dict[str, Any], _dt.datetime]]:
    async with read_scope() as session:
        rows = (
            await session.execute(
                select(ScriptOps.op_seq, ScriptOps.op_json, ScriptOps.created_at)
                .where(ScriptOps.scene_id == int(ref_id))
                .order_by(ScriptOps.op_seq.asc())
            )
        ).all()
    return [
        ({"op_seq": row[0], "op_json": row[1] or {}}, _as_datetime(row[2]))
        for row in rows
        if row[2] is not None
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


def _shot_side(row: Dict[str, Any], ledger: List[Tuple[Any, Any]]) -> Dict[str, Any]:
    prefix = _at_or_before(ledger, row.get("created_at"))
    if not prefix:
        return _unavailable(row, NO_SNAPSHOT)
    snapshot: Dict[str, Any] = {}
    for after in prefix:
        snapshot.update(after or {})
    return _side(row, text=render_shot(snapshot), available=True)


def _scene_side(row: Dict[str, Any], ledger: List[Tuple[Any, Any]]) -> Dict[str, Any]:
    prefix = _at_or_before(ledger, row.get("created_at"))
    if not prefix:
        return _unavailable(row, NO_SNAPSHOT)
    watermark = max(int(op["op_seq"]) for op in prefix)
    return _side(
        row, text=render_elements(replay_to(prefix, watermark)), available=True
    )


__all__ = [
    "NO_LEDGER",
    "NO_SNAPSHOT",
    "NOT_FOUND",
    "build_diff",
    "render_elements",
    "render_shot",
]
