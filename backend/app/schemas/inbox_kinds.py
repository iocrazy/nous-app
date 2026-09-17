"""收件箱 ``kind`` 枚举的**唯一来源**（W3d / A4）。

这个模块刻意**零依赖** —— 只有 `typing.Final`，不碰 SQLAlchemy、不碰 settings、
不碰 `app.db`。它存在的理由就是这一点：四个面都要对着同一组值，而其中两个
（`app.schemas.inbox` 与前端）本身很轻，不该为了拿一个常量元组就把数据库引擎
和配置一起拉起来。

四个面都从这里派生，`tests/api/test_inbox_kind_mirror.py` 逐处比对：

====================================  ==========================================
面                                    怎么对上
====================================  ==========================================
``app.services.notifications``        ``NotificationKind = Literal[*…]``（写入侧）
``app.schemas.inbox``                 ``InboxKind = Literal[*…]``（读出侧）
``app.models.InboxNotifications``     ``inbox_notifications_kind_check``
DB CHECK                              最后一个动过该约束的 migration
``frontend/services/notificationsService.ts``  ``export type InboxKind``
====================================  ==========================================

加一个 kind = 改这里 + 写迁移 + 改 ORM + 改前端。四处缺一个，镜像测试就红。

历史：mig 373 建了前三个，387 加 ``workflow_stage``，399 加 ``agent_question``。
在这个模块出现之前，四份各写各的、没有任何东西比对它们 —— schema 停在 373 的
三值，于是库里一行 ``agent_question`` 就把 ``GET /api/v1/inbox`` 整表打成 500。
"""

from __future__ import annotations

from typing import Final

NOTIFICATION_KINDS: Final = (
    "generation_result",
    "publish_result",
    "autopilot_output",
    "workflow_stage",
    "agent_question",
)
