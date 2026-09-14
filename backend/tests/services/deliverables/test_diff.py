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


#: ``({"after": …, "before": …}, created_at, ops_row_id)`` — the shape
#: ``_shot_ledger`` returns since the 3b fix round. The third slot is the ops
#: row's own id, which is what ``run_deliverables.ledger_ref`` points at; the
#: rows here carry no ``ledger_ref``, so these cases still exercise the
#: timestamp fallback. ``before`` is the op's pre-image — the value the field
#: had at every version BEFORE that op (tier 2 below).
SHOT_LEDGER = [
    ({"after": {"shot_type": "WS", "description": "a wide shot"}}, _at(11), 1001),
    (
        {
            "after": {"description": "a close-up"},
            "before": {"description": "a wide shot"},
        },
        _at(12),
        1002,
    ),
]

#: 分镜行**当下**的六个字段。默认全 None，所以既有用例读到的就是「账本说了算」；
#: 关心第三档的用例往这个 dict 里填值（fixture 每次调用都现读，改它即生效）。
CURRENT: dict = {}


@pytest.fixture(autouse=True)
def current_row(monkeypatch):
    """``script_shots`` 的当前行 —— 三档解析的最后一档。

    autouse：没有它，每个折叠分镜的用例都会真的去读库（那正是 3b fix A 之前
    不存在、现在存在的一个外部面）。"""
    CURRENT.clear()
    CURRENT.update({field: None for field in mod._SHOT_FIELDS})

    async def _current(ref_id):
        return dict(CURRENT)

    monkeypatch.setattr(mod, "_current_shot_fields", _current)
    return CURRENT


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


# ── 三档解析：账本 → 后继 op 的前像 → 当前行（fix A） ─────────────────────
#
# agent 的 ``UpdateShot`` 只把**改过的**那个字段写进 ``after_json``，而分镜创建
# （REST / 拆分工作流）根本不写账本行。所以「创建时设好、之后没人碰过」的字段在
# 账本里一次都不出现 —— 只折叠 ``after_json`` 的话它任何一版都重建不出来，回退
# 就会把它写成 NULL（2026-09-14 生产实测：`script_shots.shot_type` 被清空）。


#: 生产那个形状：创建没写账本行，只有一条改 description 的 update。
UNTOUCHED_LEDGER = [
    (
        {"after": {"description": "Alpha rain on glass"}, "before": {}},
        _at(11),
        2001,
    ),
    (
        {
            "after": {"description": "Beta rain on glass"},
            "before": {"description": "Alpha rain on glass"},
        },
        _at(12),
        2002,
    ),
]


def _pinned(version: int, ledger_ref: int) -> dict:
    """一行登记记录，水位钉在某一条账本行上（``ledger_ref`` 是外键，不是时间）。"""
    return {**_row(version, 11), "ledger_ref": str(ledger_ref)}


@pytest.mark.asyncio
async def test_a_field_touched_after_the_watermark_comes_from_the_next_ops_pre_image(
    monkeypatch, current_row
):
    """第二档：v1 之后那条 op 的 ``before_json`` 记着它改之前的值 —— 那正是 v1
    看到的值。拿当前行去填会把 v1 画成 v2。"""

    async def _ledger(ref_id):
        return UNTOUCHED_LEDGER

    current_row["description"] = "Beta rain on glass"
    monkeypatch.setattr(mod, "_shot_ledger", _ledger)
    content, reason = await mod.rebuild_content("script_shot", "9", _pinned(1, 2001))
    assert reason is None
    assert content["description"] == "Alpha rain on glass"


@pytest.mark.asyncio
async def test_a_field_no_op_ever_touched_comes_from_the_current_row(
    monkeypatch, current_row
):
    """第三档：账本里从没出现过 ⇒ 创建那一刻设好、此后没变过 ⇒ 当前行的值就是
    **每一版**的值。重建成 None 等于声称这一版那五个字段是空的。"""

    async def _ledger(ref_id):
        return UNTOUCHED_LEDGER

    current_row.update(
        {
            "shot_type": "MEDIUM",
            "camera_angle": "EYE_LEVEL",
            "camera_movement": "STATIC",
            "focal_length": "35mm",
            "lighting": "practical",
            "description": "Beta rain on glass",
        }
    )
    monkeypatch.setattr(mod, "_shot_ledger", _ledger)
    content, reason = await mod.rebuild_content("script_shot", "9", _pinned(1, 2001))
    assert reason is None
    assert content == {
        "shot_type": "MEDIUM",
        "camera_angle": "EYE_LEVEL",
        "camera_movement": "STATIC",
        "focal_length": "35mm",
        "lighting": "practical",
        # 第一档仍然赢过第三档：description 被 op 碰过。
        "description": "Alpha rain on glass",
    }


@pytest.mark.asyncio
async def test_the_watermark_still_wins_over_the_current_row(monkeypatch, current_row):
    """负向对照：被账本碰过的字段永远由账本说了算，哪怕当前行有别的值 —— 否则
    每一版都会退化成「当前状态」，diff 两侧一模一样。"""

    async def _ledger(ref_id):
        return UNTOUCHED_LEDGER

    current_row["description"] = "Beta rain on glass"
    monkeypatch.setattr(mod, "_shot_ledger", _ledger)
    body = await mod.build_diff(
        kind="script_shot",
        ref_id="9",
        from_row=_pinned(1, 2001),
        to_row=_pinned(2, 2002),
    )
    assert body["from"]["text"] == "description: Alpha rain on glass"
    assert body["to"]["text"] == "description: Beta rain on glass"


@pytest.mark.asyncio
async def test_an_unplaceable_ledger_row_witnesses_neither_side(
    monkeypatch, current_row
):
    """``created_at IS NULL`` + 没有 ``ledger_ref`` ⇒ 这条账本行**定位不了**。

    它进不了前缀（没法比时间），也**不该**进后缀：后缀是第二档前像的来源，而一条
    其实位于水位之下的行，它的前像是更早的状态 —— 拿它作证会把这一版画成它之前的
    样子。说不出来就不作证，退到第三档。"""
    ledger = [
        (
            {"after": {"description": "placed"}, "before": {"shot_type": "OLD"}},
            None,
            3001,
        ),
        (
            {"after": {"description": "later"}, "before": {"description": "placed"}},
            _at(12),
            3002,
        ),
    ]

    async def _ledger(ref_id):
        return ledger

    current_row["shot_type"] = "MEDIUM"
    monkeypatch.setattr(mod, "_shot_ledger", _ledger)
    content, reason = await mod.rebuild_content("script_shot", "9", _row(2, 12))
    assert reason is None
    # 无从定位的那行带着 ``before.shot_type``，但它没有资格作证 —— 第三档说了算。
    assert content["shot_type"] == "MEDIUM"
    assert content["description"] == "later"


@pytest.mark.asyncio
async def test_a_shot_row_that_is_gone_is_unavailable_rather_than_five_nulls(
    monkeypatch,
):
    """当前行读不回来（行没了 / 读失败）⇒ 第三档答不出来 ⇒ 说不知道。

    fail-closed 是刻意的：这条路的下游是回退的 UPDATE，而「当作 None」就是那次
    生产事故本身。"""

    async def _ledger(ref_id):
        return UNTOUCHED_LEDGER

    async def _gone(ref_id):
        return None

    monkeypatch.setattr(mod, "_shot_ledger", _ledger)
    monkeypatch.setattr(mod, "_current_shot_fields", _gone)
    assert await mod.rebuild_content("script_shot", "9", _pinned(1, 2001)) == (
        None,
        mod.NOT_FOUND,
    )
    body = await mod.build_diff(
        kind="script_shot",
        ref_id="9",
        from_row=_pinned(1, 2001),
        to_row=_pinned(2, 2002),
    )
    assert body["from"]["available"] is False
    assert body["from"]["unavailable_reason"] == mod.NOT_FOUND


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
