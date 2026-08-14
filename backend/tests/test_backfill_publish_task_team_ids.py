"""Unit tests for the publish-attribution backfill's pure decision logic.

The parse decision (which mirrored issue resolves back to which publish batch)
lives in a pure function precisely so it can be pinned here without a database.
"""

import pytest

from app.workflows.backfill_publish_task_team_ids import parse_publish_origin_id
from app.workflows.publish_issue_mirror import build_publish_origin_id


class TestParsePublishOriginId:
    def test_round_trips_with_the_writer(self):
        # The builder is the only definition of the prefix; if it ever changes,
        # this catches the parser going quietly blind rather than the repair
        # reporting "nothing needed fixing".
        assert parse_publish_origin_id(build_publish_origin_id("338398490984210")) == (
            "338398490984210"
        )

    def test_snowflake_stays_a_string(self):
        big = "9007199254740993"  # 2^53 + 1 — must never be numeric-coerced
        assert parse_publish_origin_id(f"publish:{big}") == big

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "publish:",  # empty id
            "publish:abc",  # non-digits would crash a numeric bind
            "publish:12x3",
            "Publish:123",  # case-sensitive, matching the writer
            "canvas:123",  # a different surface — not this backfill's business
            "project_stage:123",
            "338398490984210",  # bare id, no prefix
            123,  # not even a string
        ],
    )
    def test_rejects_everything_else(self, value):
        assert parse_publish_origin_id(value) is None
