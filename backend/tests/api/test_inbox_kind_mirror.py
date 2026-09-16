"""A4：kind 枚举的四处口径钉成一处。

四份各写各的、没有任何东西比对它们，于是 mig 387 与 399 各扩了一次 DB CHECK，
schema 停在 373 的三值，ORM 停在四值 —— 库里一行 agent_question 就把整表打成 500。
"""

from __future__ import annotations

import pathlib
import re
from importlib import import_module
from types import SimpleNamespace
from typing import get_args

import pytest
from sqlalchemy import CheckConstraint

from app.models import InboxNotifications
from app.schemas.inbox import InboxKind
from app.services.notifications import NOTIFICATION_KINDS, NotificationKind

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[3]
MIG = REPO / "supabase/migrations"
FRONTEND = REPO / "frontend/services/notificationsService.ts"


def _latest_migration_kinds() -> set[str]:
    """最后一个动过 inbox_notifications_kind_check 的迁移里那一组值。按文件号排序
    取最大 ——「最新」不能靠印象，也不能硬编码 399（下一个扩它的迁移会让硬编码
    悄悄指着旧的那一份）。"""
    hits = []
    for path in sorted(MIG.glob("*.sql")):
        m = re.search(
            r"inbox_notifications_kind_check\s+CHECK\s*\(\s*kind\s+IN\s*\(([^)]*)\)",
            path.read_text(encoding="utf-8"),
            re.IGNORECASE,
        )
        if m:
            hits.append(set(re.findall(r"'([a-z_]+)'", m.group(1))))
    assert hits, "没有任何迁移建过 inbox_notifications_kind_check —— 扫描本身坏了"
    return hits[-1]


def _frontend_kinds() -> set[str]:
    m = re.search(r"InboxKind\s*=\s*([^;]+);", FRONTEND.read_text(encoding="utf-8"))
    assert m, "notificationsService.ts 里找不到 InboxKind —— 镜像测试失去了它的对象"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def _orm_kinds() -> set[str]:
    check = next(
        c
        for c in InboxNotifications.__table__.constraints
        if isinstance(c, CheckConstraint) and c.name == "inbox_notifications_kind_check"
    )
    return set(re.findall(r"'([a-z_]+)'", str(check.sqltext)))


def test_all_four_surfaces_derive_from_the_write_side():
    assert set(get_args(NotificationKind)) == set(NOTIFICATION_KINDS)
    assert set(get_args(InboxKind)) == set(NOTIFICATION_KINDS)
    assert len(NOTIFICATION_KINDS) == len(set(NOTIFICATION_KINDS))
    expected = set(NOTIFICATION_KINDS)
    assert _orm_kinds() == expected, "ORM CheckConstraint"
    assert _latest_migration_kinds() == expected, "最新 migration 的 CHECK"
    assert _frontend_kinds() == expected, "frontend/services/notificationsService.ts"


def test_the_two_kinds_that_caused_the_500_are_actually_in_there():
    """下限断言：上面那条在四处**同时**丢掉这两个值时也会绿 —— 那正是缺陷第一天
    的样子。钉住具体的值，让扫描本身可证伪。"""
    assert {"agent_question", "workflow_stage"} <= set(NOTIFICATION_KINDS)


@pytest.mark.asyncio
async def test_one_unparseable_row_does_not_take_the_whole_list_down(monkeypatch):
    """一行坏数据（未来又多一个 kind、或某行被手工改坏）只该少一行，不该让整个
    收件箱 500 —— 与「分发器要容纳回调异常」同族。"""
    # `from app.api import inbox_router` 拿到的是 __init__.py 里同名的 APIRouter
    # 对象（`from app.api.inbox_router import router as inbox_router`），不是模块 ——
    # monkeypatch 会打到路由对象上而不是被测模块。显式按模块名取。
    inbox_router = import_module("app.api.inbox_router")

    good = {
        "id": "1",
        "kind": "agent_question",
        "title": "Agent needs input",
        "severity": "info",
    }
    bad = {"id": "2", "kind": "not_a_kind", "title": "boom", "severity": "info"}

    class _Repo:
        async def list_notifications(self, *_a, **_kw):
            return [good, bad]

        async def unread_count(self, *_a, **_kw):
            return 1

    monkeypatch.setattr(inbox_router, "get_inbox_repository", lambda: _Repo())
    res = await inbox_router.list_inbox(
        auth=SimpleNamespace(user_id="u1"), unread_only=False, limit=50, offset=0
    )
    assert [n.id for n in res.notifications] == ["1"]
    assert res.total == 1 and res.unread_count == 1
