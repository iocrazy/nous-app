from uuid import UUID

import pytest

from app.schemas.issue_message import IssueMessageKind
from app.services.issues.issue_message_mapper import map_ai_message_to_issue_message


def test_assistant_row_maps_to_agent_run():
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "role": "assistant",
        "content": "hello",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        # agent_runs.id is a BIGINT Snowflake since mig 232 → numeric string.
        "metadata_json": {"run_id": "310819108761487"},
        "created_at": "2026-05-25T00:00:00+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert m.kind == IssueMessageKind.AGENT_RUN
    assert m.body == "hello"
    assert m.author_agent_id == UUID("22222222-2222-2222-2222-222222222222")
    assert m.agent_run_id == "310819108761487"


def test_user_row_maps_to_comment_with_session_user():
    row = {
        "id": "44444444-4444-4444-4444-444444444444",
        "role": "user",
        "content": "hi",
        "metadata_json": {},
        "created_at": "2026-05-25T00:00:00+00:00",
    }
    su = UUID("55555555-5555-5555-5555-555555555555")
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=su)
    assert m.kind == IssueMessageKind.COMMENT
    assert m.author_user_id == su


# ─── attachments projection (3a Task 8a, defect 1) ──────────────────────
#
# The stored shape is what ``output_ref_resolver._stamped`` wrote and
# ``ConversationsAiStore._to_legacy_message_shape`` hands back under
# ``attachments`` — real wire shape, not an idealised one: ``title`` is
# ABSENT (not empty) when the registry row had none, and the reducer drops
# every key whose value is None.


def test_output_ref_attachment_survives_the_read_path():
    """The citation chip has to come back on reload — the whole point of
    copying the title onto the attachment at post time."""
    row = {
        "id": 323848780659604,
        "role": "user",
        "content": "look at this",
        "metadata_json": {},
        "attachments": [
            {
                "kind": "output_ref",
                "ref_kind": "script_shot",
                "ref_id": "337650953731886",
                "version": 1,
                "title": "MEDIUM",
            }
        ],
        "created_at": "2026-09-11T10:58:40.959732+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert m.attachments is not None
    att = m.attachments[0]
    assert (att.kind, att.ref_kind, att.ref_id, att.version, att.title) == (
        "output_ref",
        "script_shot",
        "337650953731886",
        1,
        "MEDIUM",
    )
    # The wire form the UI actually receives carries all five keys.
    dumped = m.model_dump()["attachments"][0]
    for key in ("kind", "ref_kind", "ref_id", "version", "title"):
        assert key in dumped


def test_output_ref_without_a_title_keeps_the_other_four_keys():
    """``_stamped`` omits ``title`` entirely when the registry row had none."""
    row = {
        "id": 1,
        "role": "user",
        "content": "x",
        "attachments": [
            {
                "kind": "output_ref",
                "ref_kind": "generated_media",
                "ref_id": "9",
                "version": 2,
            }
        ],
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert m.attachments[0].title is None
    assert m.attachments[0].version == 2


def test_resource_and_asset_ref_attachments_keep_their_own_keys():
    """Three stored kinds share one read model; none may lose its identity."""
    row = {
        "id": 2,
        "role": "user",
        "content": "x",
        "attachments": [
            {"kind": "resource_ref", "resource_id": "12345", "name": "clip.mp4"},
            {"kind": "asset_ref", "asset_id": "67890", "loadout_id": "42"},
            {"kind": "image", "mime": "image/png", "alt_text": "a cat"},
        ],
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert [a.kind for a in m.attachments] == ["resource_ref", "asset_ref", "image"]
    assert m.attachments[0].resource_id == "12345"
    assert (m.attachments[1].asset_id, m.attachments[1].loadout_id) == ("67890", "42")
    assert m.attachments[2].alt_text == "a cat"


def test_a_row_without_attachments_reads_back_as_null():
    """Chosen convention: absent → ``None``, never ``[]`` — the store itself
    returns ``body.get("attachments") or None``, and a legacy row predating
    the feature must not be dressed up as "had attachments, none left"."""
    row = {
        "id": 3,
        "role": "user",
        "content": "legacy",
        "metadata_json": {},
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert m.attachments is None
    assert m.model_dump()["attachments"] is None


def test_an_empty_attachment_list_is_also_null():
    row = {
        "id": 4,
        "role": "user",
        "content": "x",
        "attachments": [],
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    assert (
        map_ai_message_to_issue_message(
            row, issue_id=5, session_user_id=None
        ).attachments
        is None
    )


def test_a_malformed_attachments_value_does_not_break_the_thread():
    """One bad legacy row must not 500 the whole issue's history."""
    row = {
        "id": 5,
        "role": "user",
        "content": "x",
        "attachments": "not-a-list",
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    assert (
        map_ai_message_to_issue_message(
            row, issue_id=5, session_user_id=None
        ).attachments
        is None
    )


def test_assistant_rows_carry_no_attachments():
    """Only user-role messages store display attachments (the store writes
    them in ``append_user_message`` alone)."""
    row = {
        "id": 6,
        "role": "assistant",
        "content": "done",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "metadata_json": {"run_id": "310819108761487"},
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    assert (
        map_ai_message_to_issue_message(
            row, issue_id=5, session_user_id=None
        ).attachments
        is None
    )


# ─── 修复轮 1 ──────────────────────────────────────────────────────────
#
# I1 / I2 / M1 的钉子。三条都对着 CLAUDE.md 点过名的两类事故：
# 「边界 mock 必须用真实 JSON 形状」（理想化的字符串 id 让 number 分支
# 从没被跑过）与「一行畸形不该让整段历史 500」（原实现只挡住了容器类型）。


def test_snowflake_ids_that_arrive_as_json_numbers_leave_as_strings():
    """JSONB 里的 BIGINT 可能以 number 形态到达（写入方今天都先转了字符串，
    但读模型面对的是存量行，不是今天的写入方）。出口必须是字符串——
    JS 侧超过 2^53 会静默丢精度。"""
    row = {
        "id": 323848780659604,
        "role": "user",
        "content": "x",
        "attachments": [
            {
                "kind": "output_ref",
                "ref_kind": "script_shot",
                # 真实 wire 形状：number，不是被「美化」过的字符串
                "ref_id": 337650953731886,
                "version": 1,
            },
            {"kind": "resource_ref", "resource_id": 337650825568029},
            {"kind": "asset_ref", "asset_id": 348392006624870, "loadout_id": 42},
        ],
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    wire = m.model_dump()["attachments"]
    assert wire[0]["ref_id"] == "337650953731886"
    assert wire[1]["resource_id"] == "337650825568029"
    assert (wire[2]["asset_id"], wire[2]["loadout_id"]) == ("348392006624870", "42")
    # 精度真的还在（不是 str(float) 之后的近似）
    assert int(wire[0]["ref_id"]) == 337650953731886
    # version 是真 int，不受 id 强转波及
    assert wire[0]["version"] == 1 and isinstance(wire[0]["version"], int)


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "output_ref", "ref_id": "9", "version": "not-an-int"},
        {"kind": {"a": 1}},
        {"kind": "output_ref", "version": [1]},
        {"kind": "image", "title": {"t": 1}},
    ],
)
def test_one_badly_typed_attachment_does_not_500_the_thread(bad):
    """容器对了、值类型错了，是原实现漏掉的那一半：整条消息（进而整个
    issue 的历史）会在 pydantic 校验时炸掉。坏的那条丢掉，消息照常返回。"""
    row = {
        "id": 7,
        "role": "user",
        "content": "x",
        "attachments": [bad],
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert m.attachments is None
    assert m.body == "x"  # 消息本身没有丢


def test_a_good_sibling_survives_a_malformed_attachment():
    """丢的是那一条，不是整串——否则一条脏数据就会让引用 chip 集体消失。"""
    row = {
        "id": 8,
        "role": "user",
        "content": "x",
        "attachments": [
            {"kind": "output_ref", "ref_kind": "script_shot", "version": "oops"},
            {
                "kind": "output_ref",
                "ref_kind": "script_shot",
                "ref_id": "337650953731886",
                "version": 1,
                "title": "MEDIUM",
            },
        ],
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    m = map_ai_message_to_issue_message(row, issue_id=5, session_user_id=None)
    assert len(m.attachments) == 1
    assert m.attachments[0].ref_id == "337650953731886"


def test_a_dropped_attachment_says_which_message_and_which_field():
    """静默丢弃会让写入侧的漂移永远查不出来。WARNING 里要能定位到行，
    也要说清是哪个字段不对。（loguru 不走 stdlib logging，所以挂自己的
    sink，不用 caplog。）"""
    from loguru import logger as loguru_logger

    seen: list[str] = []
    sink_id = loguru_logger.add(seen.append, level="WARNING", format="{message}")
    try:
        row = {
            "id": 323848780659604,
            "role": "user",
            "content": "x",
            "attachments": [{"kind": "output_ref", "version": "not-an-int"}],
            "created_at": "2026-09-11T10:58:40+00:00",
        }
        map_ai_message_to_issue_message(
            row, issue_id=348392006624870, session_user_id=None
        )
    finally:
        loguru_logger.remove(sink_id)

    logged = "".join(seen)
    assert "323848780659604" in logged  # message id
    assert "348392006624870" in logged  # issue id
    assert "version" in logged  # 哪个字段


def test_bytes_and_urls_are_not_handed_back_even_if_a_row_carries_them():
    """`data_url` / `url` / `scope` 从不该进 store（字节不进消息体）。
    万一某行带着它们，读回路也不许交出去——这是第二道白名单的钉子。"""
    row = {
        "id": 9,
        "role": "user",
        "content": "x",
        "attachments": [
            {
                "kind": "image",
                "mime": "image/png",
                "data_url": "data:image/png;base64,AAAA",
                "url": "https://example.test/a.png",
                "scope": {"team_id": "1"},
            }
        ],
        "created_at": "2026-09-11T10:58:40+00:00",
    }
    wire = map_ai_message_to_issue_message(
        row, issue_id=5, session_user_id=None
    ).model_dump()
    att = wire["attachments"][0]
    assert att["kind"] == "image" and att["mime"] == "image/png"
    for forbidden in ("data_url", "url", "scope"):
        assert forbidden not in att


def test_the_read_model_mirrors_the_stores_attachment_whitelist():
    """两份白名单没有任何机制绑在一起：store 加一个键 → 读模型静默丢弃；
    读模型加一个 store 不存的键 → 永远 null。两种漂移都不会有人说出来，
    所以在这里比一次（同 assets slot 表的前后端镜像测试）。"""
    from app.schemas.issue_message import IssueMessageAttachment
    from app.services.ai.chat.conversations_ai_store import (
        _DISPLAY_ATTACHMENT_KEYS,
    )

    assert set(IssueMessageAttachment.model_fields) == set(_DISPLAY_ATTACHMENT_KEYS)
