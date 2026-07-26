"""Regression: Snowflake int IDs must be coerced before hitting task_tracking.

2026-07-26 — a retried download ran to completion (DBOS dispatched, yt-dlp
downloaded) but never appeared in the Task Center. Two layers failed to create
the row and both only logged a warning:

    [Download/Dedup] Pre-create unified_task failed: asyncpg.DataError:
      invalid input for query argument $6: 331610444857441 (expected str, got int)
    [TaskManager] start() self-heal create failed for <wf> (continuing): ...

`task_tracking.resource_id` / `.media_id` are **text** columns, and
`_build_row`'s signature already says `Optional[str]` — but callers hand it
the Snowflake BIGINT as a Python int (project-wide, IDs are ints; see
CLAUDE.md "所有 ID 使用 Snowflake BIGINT"). asyncpg type-checks bind params
strictly, so an int for a ::VARCHAR param raises DataError before the INSERT
is ever sent.

Failure mode is silent by construction: the caller catches, logs a warning
and continues, so the workflow runs normally and only the UI row is missing.
Same shape as the `dbos_workflow_id` NOT NULL guard already in `_build_row`
("without it the INSERT 23502s and the task silently never reaches the Task
Center") — coercion belongs at that same boundary, not in each caller.

Not hypothetical: this warning has recurred since 2026-06-03 (29 occurrences
on 07-08 alone), meaning a batch of historical tasks never reached the Task
Center and nobody noticed — the only symptom is "nothing showed up".
"""

from __future__ import annotations

from app.services.infra.unified_task_manager import UnifiedTaskManager

_USER = "8e1584e3-9c29-4a5b-90fe-125b74259f7f"


def test_build_row_coerces_int_resource_id_to_str() -> None:
    """The exact value from the 2026-07-26 failure."""
    row = UnifiedTaskManager._build_row(
        user_id=_USER,
        task_type="download",
        title="Test Download",
        resource_id=331610444857441,
        media_id="bilibili_BV1o2g26XE4x",
    )

    assert row["resource_id"] == "331610444857441"
    assert isinstance(row["resource_id"], str), (
        "task_tracking.resource_id is a text column bound as ::VARCHAR; "
        "asyncpg rejects int with DataError before the INSERT is sent."
    )


def test_build_row_coerces_int_media_id_to_str() -> None:
    """media_id takes Snowflake ints too (parse path passes parsed_media.id)."""
    row = UnifiedTaskManager._build_row(
        user_id=_USER,
        task_type="ai_transcription",
        title="Test Transcription",
        media_id=331610444669024,
    )

    assert row["media_id"] == "331610444669024"
    assert isinstance(row["media_id"], str)


def test_build_row_leaves_str_ids_untouched() -> None:
    """Platform-style string ids must survive unchanged — no double-casting."""
    row = UnifiedTaskManager._build_row(
        user_id=_USER,
        task_type="download",
        title="Test",
        resource_id="331610444857441",
        media_id="bilibili_BV1o2g26XE4x",
    )

    assert row["resource_id"] == "331610444857441"
    assert row["media_id"] == "bilibili_BV1o2g26XE4x"


def test_build_row_omits_absent_ids() -> None:
    """None must stay absent from the row, not become the string 'None'."""
    row = UnifiedTaskManager._build_row(
        user_id=_USER,
        task_type="upload",
        title="Test Upload",
    )

    assert "resource_id" not in row
    assert "media_id" not in row


def test_build_row_coerces_int_group_id_to_str() -> None:
    """group_id is the same shape of column and takes the same caller ints."""
    row = UnifiedTaskManager._build_row(
        user_id=_USER,
        task_type="download",
        title="Test",
        group_id=331610444857442,
    )

    assert row["group_id"] == "331610444857442"
