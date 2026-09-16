"""A4：kind 枚举的四处口径钉成一处。

四份各写各的、没有任何东西比对它们，于是 mig 387 与 399 各扩了一次 DB CHECK，
schema 停在 373 的三值，ORM 停在四值 —— 库里一行 agent_question 就把整表打成 500。
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
from importlib import import_module
from types import SimpleNamespace
from typing import get_args

import pytest
from sqlalchemy import CheckConstraint

from app.models import InboxNotifications
from app.schemas.inbox import InboxKind
from app.schemas.inbox_kinds import NOTIFICATION_KINDS
from app.services.notifications import NotificationKind

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[3]
MIG = REPO / "supabase/migrations"
FRONTEND = REPO / "frontend/services/notificationsService.ts"


def _migration_number(path: pathlib.Path) -> int:
    """迁移文件号。按**数字**排，不按文件名字符串排 —— 三位数迁移用完之后
    `'1000_x.sql' < '999_x.sql'`，字符串序会让「最新」悄悄指着旧的那一份。
    取不到数字的（如 schema_baseline.sql）排在最前，它们不会是最新的那个。"""
    head = path.name.split("_")[0]
    return int(head) if head.isdigit() else -1


def _latest_migration_kinds() -> set[str]:
    """最后一个动过 inbox_notifications_kind_check 的迁移里那一组值。按文件号排序
    取最大 ——「最新」不能靠印象，也不能硬编码 399（下一个扩它的迁移会让硬编码
    悄悄指着旧的那一份）。"""
    hits = []
    for path in sorted(MIG.glob("*.sql"), key=_migration_number):
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


def _router_module():
    """`from app.api import inbox_router` 拿到的是 __init__.py 里同名的 APIRouter
    对象（`from app.api.inbox_router import router as inbox_router`），不是模块 ——
    monkeypatch 会打到路由对象上而不是被测模块。显式按模块名取。"""
    return import_module("app.api.inbox_router")


class _ErrorRecorder:
    """替掉模块里的 loguru logger，只留我们要断言的那一面。"""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, message: str, *_a, **_kw) -> None:
        self.errors.append(message)


async def _call_list_inbox(monkeypatch, rows, *, unread=1):
    mod = _router_module()

    class _Repo:
        async def list_notifications(self, *_a, **_kw):
            return list(rows)

        async def unread_count(self, *_a, **_kw):
            return unread

    recorder = _ErrorRecorder()
    monkeypatch.setattr(mod, "get_inbox_repository", lambda: _Repo())
    monkeypatch.setattr(mod, "logger", recorder)
    res = await mod.list_inbox(
        auth=SimpleNamespace(user_id="u1"), unread_only=False, limit=50, offset=0
    )
    return res, recorder


GOOD_ROW = {
    "id": "1",
    "kind": "agent_question",
    "title": "Agent needs input",
    "severity": "info",
}


@pytest.mark.asyncio
async def test_one_unparseable_row_does_not_take_the_whole_list_down(monkeypatch):
    """一行坏数据（未来又多一个 kind、或某行被手工改坏）只该少一行，不该让整个
    收件箱 500 —— 与「分发器要容纳回调异常」同族。"""
    bad = {"id": "2", "kind": "not_a_kind", "title": "boom", "severity": "info"}
    res, _ = await _call_list_inbox(monkeypatch, [GOOD_ROW, bad])

    assert [n.id for n in res.notifications] == ["1"]
    assert res.total == 1 and res.unread_count == 1


@pytest.mark.asyncio
async def test_the_whole_page_reports_once_not_once_per_bad_row(monkeypatch):
    """三行坏数据发**一条** ERROR，不是三条。一张坏掉的表会按页刷屏，把同一个
    事实重复几十遍，真正该被看见的别的错误就被埋了。"""
    bad = [
        {"id": str(i), "kind": "not_a_kind", "title": "boom", "severity": "info"}
        for i in (2, 3, 4)
    ]
    res, recorder = await _call_list_inbox(monkeypatch, [GOOD_ROW, *bad])

    assert res.total == 1
    assert len(recorder.errors) == 1, recorder.errors
    line = recorder.errors[0]
    assert "3" in line and "not_a_kind" in line and "kind" in line and "u1" in line


@pytest.mark.asyncio
async def test_a_clean_page_says_nothing_at_all(monkeypatch):
    """负向对照：没有坏行就不该有 ERROR —— 否则上面那条用「总是记一条」也能绿。"""
    res, recorder = await _call_list_inbox(monkeypatch, [GOOD_ROW])

    assert res.total == 1
    assert recorder.errors == []


@pytest.mark.asyncio
async def test_the_error_line_does_not_carry_the_notification_body(monkeypatch):
    """pydantic v2 的 errors() 每条都带 ``input``（出错字段的**原值**），``str(exc)``
    会把它渲染进消息。通知正文是用户内容，不该进日志 —— 只记 kind 与出错字段名。

    ⚠️ 这条用例必须让**承载 secret 的那个字段自己校验失败**。先前写成
    ``title=secret`` + kind 坏，跑绿却毫无意义：title 是合法字符串、根本不会被
    pydantic 报出来，于是「把 str(exc) 塞进日志」这个突变照样通过。
    这里让 body 拿到一个非字符串（schema 收紧、或上游给了 JSON 对象时的真实形状），
    它的原值就会进 errors()，泄漏路径才真的被这条断言盖住。

    ⚠️ token 必须**短**。pydantic 渲染 ``input_value`` 时对长值做中段截断
    （``'Dinner with Dan...rce papers are signed'``），所以拿一整句长文本做
    ``in`` 断言永远不会命中 —— 那会让这条用例在真泄漏时照样绿。"""
    secret = "Dana7Q4X"
    bad = {
        "id": "2",
        "kind": "not_a_kind",
        "title": "boom",
        "body": {"note": secret},
        "severity": "info",
    }
    _, recorder = await _call_list_inbox(monkeypatch, [bad])

    assert len(recorder.errors) == 1
    line = recorder.errors[0]
    assert secret not in line
    # 出错的字段名要在，否则这条日志无法指认坏在哪
    assert "kind" in line and "body" in line


def test_importing_the_response_schema_does_not_drag_in_the_database():
    """`app.schemas.inbox` 只是一组 Pydantic 响应模型。kind 常量若放在写入侧的
    `services.notifications`，import 它就会连带拉起 SQLAlchemy、引擎与 settings
    （评审实测首次 import 1.5 s）—— 所以常量住在零依赖的 `schemas.inbox_kinds`。

    必须在**子进程**里验：本测试会话早就把这些模块 import 进 sys.modules 了，
    在进程内查等于问一个已经被污染的问题。"""
    heavy = ("app.db.engine", "app.db.session", "app.core.config", "sqlalchemy")
    probe = (
        "import json, sys; import app.schemas.inbox; "
        f"print(json.dumps([m for m in {heavy!r} if m in sys.modules]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=REPO / "backend",
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [], proc.stdout
