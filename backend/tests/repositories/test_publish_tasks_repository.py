import pytest

from app.repositories.publish_tasks_repository import (
    _public_account_row,
    _public_task_row,
    aggregate_task_status,
)


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (["success", "success"], "success"),
        (["failed", "failed"], "failed"),
        (["success", "failed"], "partial"),
        (["pending_share", "success"], "pending_share"),
        (["publishing", "pending"], "publishing"),
        (["pending", "pending"], "pending"),
        (["cancelled", "cancelled"], "failed"),
        ([], "pending"),
    ],
)
def test_aggregate_task_status(statuses, expected):
    assert aggregate_task_status(statuses) == expected


def test_public_task_row_stringifies_bigints():
    pub = _public_task_row(
        {"id": 727145299382534145, "team_id": 727145299382534200, "title": "x"}
    )
    assert pub["id"] == "727145299382534145"
    assert pub["team_id"] == "727145299382534200"
    assert pub["title"] == "x"


def test_public_account_row_stringifies_and_keeps_status():
    pub = _public_account_row(
        {"id": 1, "account_id": 727145299382534146, "status": "pending_share"}
    )
    assert pub["id"] == "1" and pub["account_id"] == "727145299382534146"
    assert pub["status"] == "pending_share"
