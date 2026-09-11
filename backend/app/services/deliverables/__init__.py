"""产出登记（harness p4 §1-④ / 三期 3a）。

对外只有一个名字：``register_deliverable``。没登记 = 不存在。
"""

from app.services.deliverables.kinds import ALL_KINDS, TITLE_MAX, DeliverableKind
from app.services.deliverables.registry import DeliverableRow, register_deliverable

__all__ = [
    "ALL_KINDS",
    "TITLE_MAX",
    "DeliverableKind",
    "DeliverableRow",
    "register_deliverable",
]
