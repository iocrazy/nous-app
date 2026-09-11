"""Reconstructing two versions of one产出 as content (三期 3a spec §4).

``run_deliverables`` records THAT a version happened, never its bytes, so each
side is folded back out of the ledger that already exists for that kind. The
two facts this file pins:

* a version's content is the ledger **at or before its own ``created_at``** —
  fold the whole ledger into every side and v1 would show v3's text;
* a side that cannot be reconstructed says ``available: False`` with a typed
  reason. An empty string would be drawn as "this version was blank", which is
  a different and wrong claim.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from app.services.deliverables import diff as mod

pytestmark = pytest.mark.unit

RUN_ID = "913402881190401"


def _at(day: int) -> _dt.datetime:
    return _dt.datetime(2026, 9, day, tzinfo=_dt.timezone.utc)


def _row(version: int, day: int) -> dict:
    return {
        "version": version,
        "run_id": RUN_ID,
        "issue_id": "348087075560200",
        "created_at": _at(day).isoformat(),
        "model": "qwen-max",
        "cost_cents": 1.25,
        "title": f"v{version}",
    }


# ── pure rendering ───────────────────────────────────────────────────────


def test_render_shot_keeps_a_stable_field_order_and_drops_empties():
    text = mod.render_shot(
        {"description": "a wide shot", "shot_type": "WS", "lighting": ""}
    )
    assert text == "shot_type: WS\ndescription: a wide shot"


def test_render_elements_is_one_line_per_element():
    text = mod.render_elements(
        [
            {"type": "action", "text": " He enters. "},
            {"type": "dialogue", "text": "Hello."},
            {"type": "action"},
        ]
    )
    assert text == "action: He enters.\ndialogue: Hello.\naction: "


def test_render_elements_tolerates_an_empty_scene():
    assert mod.render_elements([]) == ""
    assert mod.render_elements(None) == ""


# ── script_shot ──────────────────────────────────────────────────────────


SHOT_LEDGER = [
    ({"shot_type": "WS", "description": "a wide shot"}, _at(11)),
    ({"description": "a close-up"}, _at(12)),
]


@pytest.mark.asyncio
async def test_a_shot_version_sees_only_the_ledger_up_to_its_own_time(monkeypatch):
    async def _ledger(ref_id):
        return SHOT_LEDGER

    monkeypatch.setattr(mod, "_shot_ledger", _ledger)
    body = await mod.build_diff(
        kind="script_shot", ref_id="9", from_row=_row(1, 11), to_row=_row(2, 12)
    )
    assert body["content_type"] == "text"
    assert body["from"]["text"] == "shot_type: WS\ndescription: a wide shot"
    # The update row carries only the field it changed; folding it onto the
    # create is what makes the second pane a full snapshot rather than a
    # one-line fragment.
    assert body["to"]["text"] == "shot_type: WS\ndescription: a close-up"
    assert body["from"]["available"] and body["to"]["available"]


@pytest.mark.asyncio
async def test_a_shot_version_older_than_every_ledger_row_is_unavailable(monkeypatch):
    async def _ledger(ref_id):
        return SHOT_LEDGER

    monkeypatch.setattr(mod, "_shot_ledger", _ledger)
    body = await mod.build_diff(
        kind="script_shot", ref_id="9", from_row=_row(1, 1), to_row=_row(2, 12)
    )
    assert body["from"]["available"] is False
    assert body["from"]["unavailable_reason"] == mod.NO_SNAPSHOT
    assert body["from"]["text"] is None
    assert body["to"]["available"] is True


@pytest.mark.asyncio
async def test_each_side_carries_its_own_metadata(monkeypatch):
    async def _ledger(ref_id):
        return SHOT_LEDGER

    monkeypatch.setattr(mod, "_shot_ledger", _ledger)
    body = await mod.build_diff(
        kind="script_shot", ref_id="9", from_row=_row(1, 11), to_row=_row(2, 12)
    )
    assert body["from"]["version"] == 1 and body["to"]["version"] == 2
    assert body["to"]["run_id"] == RUN_ID
    assert body["to"]["issue_id"] == "348087075560200"
    assert body["to"]["cost_cents"] == 1.25


# ── script_scene ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_scene_version_is_replayed_to_its_own_watermark(monkeypatch):
    ledger = [
        (
            {
                "op_seq": 1,
                "op_json": {
                    "ops": [
                        {
                            "op": "insert",
                            "element_id": "el_1",
                            "payload": {"type": "action", "text": "He enters."},
                        }
                    ]
                },
            },
            _at(11),
        ),
        (
            {
                "op_seq": 2,
                "op_json": {
                    "ops": [
                        {
                            "op": "insert",
                            "element_id": "el_2",
                            "after_id": "el_1",
                            "payload": {"type": "dialogue", "text": "Hello."},
                        }
                    ]
                },
            },
            _at(12),
        ),
    ]

    async def _ledger(ref_id):
        return ledger

    monkeypatch.setattr(mod, "_scene_ledger", _ledger)
    body = await mod.build_diff(
        kind="script_scene", ref_id="7", from_row=_row(1, 11), to_row=_row(2, 12)
    )
    assert body["from"]["text"] == "action: He enters."
    assert body["to"]["text"] == "action: He enters.\ndialogue: Hello."


# ── generated_media ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_media_sides_carry_the_row_and_both_same_origin_urls(monkeypatch):
    async def _media(ref_id):
        return {
            "id": "500",
            "media_kind": "image",
            "mime": "image/png",
            "cover_url": "/api/v1/generated-media/500/cover",
            "stream_url": "/api/v1/generated-media/500/stream",
        }

    monkeypatch.setattr(mod, "_media_row", _media)
    body = await mod.build_diff(
        kind="generated_media", ref_id="500", from_row=_row(1, 11), to_row=_row(1, 11)
    )
    assert body["content_type"] == "media"
    assert body["from"]["media"]["cover_url"] == "/api/v1/generated-media/500/cover"
    assert body["to"]["media"]["id"] == "500"
    assert body["from"]["text"] is None


@pytest.mark.asyncio
async def test_a_deleted_media_row_is_unavailable_not_a_crash(monkeypatch):
    async def _media(ref_id):
        return None

    monkeypatch.setattr(mod, "_media_row", _media)
    body = await mod.build_diff(
        kind="generated_media", ref_id="500", from_row=_row(1, 11), to_row=_row(1, 11)
    )
    assert body["from"]["available"] is False
    assert body["from"]["unavailable_reason"] == mod.NOT_FOUND


# ── the kinds with no ledger, and failures ───────────────────────────────


@pytest.mark.asyncio
async def test_a_chapter_says_it_has_no_ledger_rather_than_faking_one():
    body = await mod.build_diff(
        kind="script_chapter", ref_id="3", from_row=_row(1, 11), to_row=_row(2, 12)
    )
    assert body["from"]["unavailable_reason"] == mod.NO_LEDGER
    assert body["to"]["unavailable_reason"] == mod.NO_LEDGER


@pytest.mark.asyncio
async def test_a_broken_ledger_read_yields_two_unavailable_sides(monkeypatch):
    """The versions are real — the registry says so. A 500 here would hide
    that the object has a lineage at all."""

    async def _boom(ref_id):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(mod, "_shot_ledger", _boom)
    body = await mod.build_diff(
        kind="script_shot", ref_id="9", from_row=_row(1, 11), to_row=_row(2, 12)
    )
    assert body["from"]["available"] is False
    assert body["from"]["unavailable_reason"] == mod.NOT_FOUND
    assert body["to"]["version"] == 2
