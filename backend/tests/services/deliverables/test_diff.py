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


#: ``(after_json, created_at, ops_row_id)`` — the shape ``_shot_ledger``
#: returns since 3b. The third slot is the ops row's own id, which is what
#: ``run_deliverables.ledger_ref`` points at; the rows here carry no
#: ``ledger_ref``, so these cases still exercise the timestamp fallback.
SHOT_LEDGER = [
    ({"shot_type": "WS", "description": "a wide shot"}, _at(11), 1001),
    ({"description": "a close-up"}, _at(12), 1002),
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
            1,
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
            2,
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


# ── rebuild_content（3b 回退写回的就是它） ────────────────────────────────


SCENE_LEDGER = [
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
        1,
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
        2,
    ),
]


@pytest.mark.asyncio
async def test_a_rebuilt_shot_is_every_writable_field_not_just_the_filled_ones(
    monkeypatch,
):
    """回退把这个 dict 直接 UPDATE 进 ``script_shots``，所以它必须是**完整的**六
    个字段——只带折叠时出现过的键，会让一次回退把没被提到的列原地留在新版上。"""

    async def _ledger(ref_id):
        return SHOT_LEDGER

    monkeypatch.setattr(mod, "_shot_ledger", _ledger)
    content, reason = await mod.rebuild_content("script_shot", "9", _row(2, 12))
    assert reason is None
    assert content == {
        "shot_type": "WS",
        "camera_angle": None,
        "camera_movement": None,
        "focal_length": None,
        "lighting": None,
        "description": "a close-up",
    }


@pytest.mark.asyncio
async def test_a_rebuilt_shot_and_its_diff_side_are_the_same_fold(monkeypatch):
    """两套重建 = 两种「v1 是什么」的说法，而回退会把其中一种当真写进库。"""

    async def _ledger(ref_id):
        return SHOT_LEDGER

    monkeypatch.setattr(mod, "_shot_ledger", _ledger)
    row = _row(1, 11)
    content, _ = await mod.rebuild_content("script_shot", "9", row)
    body = await mod.build_diff(
        kind="script_shot", ref_id="9", from_row=row, to_row=row
    )
    assert mod.render_shot(content) == body["from"]["text"]


@pytest.mark.asyncio
async def test_a_rebuilt_scene_is_the_element_array_at_that_watermark(monkeypatch):
    async def _ledger(ref_id):
        return SCENE_LEDGER

    monkeypatch.setattr(mod, "_scene_ledger", _ledger)
    content, reason = await mod.rebuild_content("script_scene", "7", _row(1, 11))
    assert reason is None
    assert [e["text"] for e in content] == ["He enters."]


@pytest.mark.asyncio
async def test_a_version_older_than_every_ledger_row_rebuilds_to_nothing(monkeypatch):
    async def _ledger(ref_id):
        return SHOT_LEDGER

    monkeypatch.setattr(mod, "_shot_ledger", _ledger)
    assert await mod.rebuild_content("script_shot", "9", _row(1, 1)) == (
        None,
        mod.NO_SNAPSHOT,
    )


@pytest.mark.asyncio
async def test_a_kind_with_no_ledger_says_so_rather_than_returning_a_blank():
    """媒体重生成是新对象的 v1、章节没有账本。``(None, reason)`` 让调用方 409，
    而 ``({}, None)`` 会让一次回退把六个字段全清空。"""
    for kind in ("generated_media", "script_chapter"):
        assert await mod.rebuild_content(kind, "5", _row(1, 11)) == (
            None,
            mod.NO_LEDGER,
        )


@pytest.mark.asyncio
async def test_a_broken_ledger_read_rebuilds_to_nothing_not_an_exception(monkeypatch):
    async def _boom(ref_id):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(mod, "_shot_ledger", _boom)
    assert await mod.rebuild_content("script_shot", "9", _row(2, 12)) == (
        None,
        mod.NOT_FOUND,
    )
