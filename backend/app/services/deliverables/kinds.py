"""产出的四类。新增一类必须同时给出 ref_id 的取法与标题的取法——
没有这两样，血缘端点就只能显示一个裸 id。
"""

from typing import Final, Literal

DeliverableKind = Literal[
    "generated_media", "script_shot", "script_scene", "script_chapter"
]

ALL_KINDS: Final[tuple[str, ...]] = (
    "generated_media",
    "script_shot",
    "script_scene",
    "script_chapter",
)

TITLE_MAX: Final[int] = 120

__all__ = ["ALL_KINDS", "TITLE_MAX", "DeliverableKind"]
