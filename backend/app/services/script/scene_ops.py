"""Anchor-based element-op protocol core (spec v3 §2.2).

Pure Python, no IO, no framework imports. This module is the single source of
truth for how a scene's ``content_json`` (an ordered list of element dicts)
mutates in response to a batch of ops, and for the exact inverse batch that
undoes it.

An *element* is a dict shaped ``{"id": "el_x", "type": <ELEMENT_TYPES>,
"text": ..., "character_id": ...}``. An *op* is a dict shaped
``{"op": "insert|update|delete|move", "element_id": "el_x",
"before_id": str|None, "after_id": str|None, "payload": {...}}``.

``apply_ops`` never mutates its inputs (immutability contract): it works on a
deep copy and returns fresh structures. The returned ``inverse_ops`` is ordered
so that ``apply_ops(apply_ops(elements, ops)[0], inverse_ops)[0] == elements``.
"""

import copy
from typing import Optional

OP_ERROR_CODES = frozenset(
    {
        "missing_anchor",
        "unknown_element",
        "duplicate_id",
        "invalid_op",
        "invalid_payload",
    }
)

ELEMENT_TYPES = frozenset(
    {"action", "dialogue", "character", "paren", "transition", "comment", "subtitle"}
)

_VALID_OPS = frozenset({"insert", "update", "delete", "move"})


class OpError(Exception):
    """Raised when an op is malformed or references something that isn't there.

    ``code`` is one of ``OP_ERROR_CODES``; ``message`` is human-readable.
    """

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _index_of(elements: list, element_id: str) -> int:
    """Return the index of ``element_id`` in ``elements`` or -1 if absent."""
    for i, el in enumerate(elements):
        if el.get("id") == element_id:
            return i
    return -1


def _payload_without_id(element: dict) -> dict:
    """Return a deep copy of ``element`` minus its ``id`` key."""
    return {k: copy.deepcopy(v) for k, v in element.items() if k != "id"}


def _anchor_index(
    elements: list, before_id: Optional[str], after_id: Optional[str]
) -> Optional[int]:
    """Resolve an insertion index from the anchors (``before_id`` wins).

    Returns the target index, or ``None`` when no anchor is supplied (caller
    appends at the tail). Raises ``OpError("missing_anchor")`` when an anchor is
    supplied but does not resolve to an existing element.
    """
    if before_id is not None:
        idx = _index_of(elements, before_id)
        if idx < 0:
            raise OpError("missing_anchor", f"before_id {before_id!r} not found")
        return idx
    if after_id is not None:
        idx = _index_of(elements, after_id)
        if idx < 0:
            raise OpError("missing_anchor", f"after_id {after_id!r} not found")
        return idx + 1
    return None


def _neighbor_anchor(elements: list, index: int) -> tuple:
    """Anchors that pin ``index``'s position for a later re-insert.

    Returns ``(before_id, after_id)`` where ``before_id`` is the id of the
    element currently after ``index`` (re-insert before it lands at ``index``),
    falling back to ``after_id`` = the element before ``index`` (re-insert after
    it lands at the tail / original spot). Both ``None`` means the list held a
    single element.
    """
    next_id = elements[index + 1]["id"] if index + 1 < len(elements) else None
    prev_id = elements[index - 1]["id"] if index - 1 >= 0 else None
    if next_id is not None:
        return next_id, None
    return None, prev_id


def _op_insert(elements: list, op: dict) -> Optional[dict]:
    element_id = op.get("element_id")
    if not element_id:
        raise OpError("invalid_payload", "insert requires element_id")
    payload = op.get("payload") or {}
    el_type = payload.get("type")
    if el_type not in ELEMENT_TYPES:
        raise OpError("invalid_payload", f"unknown element type {el_type!r}")

    existing = _index_of(elements, element_id)
    if existing >= 0:
        # Upsert-in-place: replace payload, never move. Inverse restores the
        # full prior element via the same upsert path.
        prior_payload = _payload_without_id(elements[existing])
        elements[existing] = {"id": element_id, **copy.deepcopy(payload)}
        return {"op": "insert", "element_id": element_id, "payload": prior_payload}

    idx = _anchor_index(elements, op.get("before_id"), op.get("after_id"))
    new_el = {"id": element_id, **copy.deepcopy(payload)}
    if idx is None:
        elements.append(new_el)
    else:
        elements.insert(idx, new_el)
    return {"op": "delete", "element_id": element_id}


def _op_update(elements: list, op: dict) -> Optional[dict]:
    element_id = op.get("element_id")
    if not element_id:
        raise OpError("invalid_payload", "update requires element_id")
    idx = _index_of(elements, element_id)
    if idx < 0:
        raise OpError("unknown_element", f"element {element_id!r} not found")
    payload = op.get("payload") or {}
    prior = {k: copy.deepcopy(elements[idx].get(k)) for k in payload}
    elements[idx].update(copy.deepcopy(payload))
    return {"op": "update", "element_id": element_id, "payload": prior}


def _op_delete(elements: list, op: dict) -> Optional[dict]:
    element_id = op.get("element_id")
    if not element_id:
        raise OpError("invalid_payload", "delete requires element_id")
    idx = _index_of(elements, element_id)
    if idx < 0:
        # Idempotent: deleting a missing element is a no-op with no inverse.
        return None
    before_id, after_id = _neighbor_anchor(elements, idx)
    prior_payload = _payload_without_id(elements[idx])
    del elements[idx]
    return {
        "op": "insert",
        "element_id": element_id,
        "payload": prior_payload,
        "before_id": before_id,
        "after_id": after_id,
    }


def _op_move(elements: list, op: dict) -> Optional[dict]:
    element_id = op.get("element_id")
    if not element_id:
        raise OpError("invalid_payload", "move requires element_id")
    idx = _index_of(elements, element_id)
    if idx < 0:
        raise OpError("unknown_element", f"element {element_id!r} not found")

    before_id = op.get("before_id")
    after_id = op.get("after_id")
    if before_id is None and after_id is None:
        raise OpError("missing_anchor", "move requires before_id or after_id")
    # Anchor pointing at the element itself is a no-op.
    if before_id == element_id or after_id == element_id:
        return None
    # Validate the anchor exists before touching anything.
    if before_id is not None and _index_of(elements, before_id) < 0:
        raise OpError("missing_anchor", f"before_id {before_id!r} not found")
    if before_id is None and _index_of(elements, after_id) < 0:
        raise OpError("missing_anchor", f"after_id {after_id!r} not found")

    orig_before, orig_after = _neighbor_anchor(elements, idx)
    element = elements.pop(idx)
    target = _anchor_index(elements, before_id, after_id)
    if target is None:
        elements.append(element)
    else:
        elements.insert(target, element)
    return {
        "op": "move",
        "element_id": element_id,
        "before_id": orig_before,
        "after_id": orig_after,
    }


_DISPATCH = {
    "insert": _op_insert,
    "update": _op_update,
    "delete": _op_delete,
    "move": _op_move,
}


def apply_ops(elements: list, ops: list) -> tuple:
    """Apply ``ops`` to a copy of ``elements``; return ``(new_elements, inverse_ops)``.

    Anchor-based, immutable (the input list and its element dicts are never
    mutated). ``inverse_ops`` is ordered such that applying it to
    ``new_elements`` reproduces the original ``elements`` exactly.
    """
    working = copy.deepcopy(elements)
    inverses = []
    for op in ops:
        name = op.get("op")
        handler = _DISPATCH.get(name)
        if handler is None:
            raise OpError("invalid_op", f"unknown op {name!r}")
        inverse = handler(working, op)
        if inverse is not None:
            inverses.append(inverse)
    inverses.reverse()  # undo last-applied first
    return working, inverses


def extract_text(elements: list) -> str:
    """Derive plain text from elements: join in order, one per line.

    Character elements get a trailing colon (screenplay cue convention);
    elements with empty/absent text are skipped.
    """
    lines = []
    for el in elements:
        text = el.get("text") or ""
        if not text:
            continue
        if el.get("type") == "character":
            text = f"{text}:"
        lines.append(text)
    return "\n".join(lines)
