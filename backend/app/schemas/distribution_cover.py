"""Distribution — 封面抽帧 / 选帧的请求与响应模型。

两个动作的形状差异很大，所以分成两组：抽帧是**异步**的（返回 task_id，前端
订阅 task_tracking Realtime 拿候选清单），选帧是**同步**的（返回两个 resource
id，前端立刻能用）。理由见 ``app/workflows/cover_frames.py`` 的模块 docstring。

BIGINT id 一律用 str 序列化 —— 与 ``distribution_publish`` 同款，JS 的 2^53
放不下 snowflake。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.services.distribution.cover_frames import (
    DEFAULT_COVER_FRAMES,
    MAX_COVER_FRAMES,
)


class CoverExtractRequest(BaseModel):
    """从一个视频 resource 抽候选封面帧。"""

    resource_id: str = Field(description="Video resource to sample frames from")
    num_frames: int = Field(
        default=DEFAULT_COVER_FRAMES,
        ge=1,
        le=MAX_COVER_FRAMES,
        description=(
            "How many evenly-spaced frames to sample. Each becomes a resource "
            "row, so the ceiling is deliberately lower than the extractor's own."
        ),
    )


class CoverExtractResponse(BaseModel):
    """``task_id`` 同时是 task_tracking 行的主键与 DBOS workflow id。前端订阅
    这一行，成功时 ``metadata.cover_frames.candidates`` 就是候选清单。"""

    task_id: str


class CoverCandidateOut(BaseModel):
    """一个候选帧。前端用 ``resource_id`` 走普通 media URL 取图。

    这个模型目前不出现在任何端点的 response_model 上 —— 候选清单是经
    ``task_tracking.metadata`` 走 Realtime 送达的，不是 HTTP 响应。放在这里是
    为了让那份 jsonb 的形状有一处**声明式**的契约，前端类型照它写，改字段时
    两边能对上。
    """

    resource_id: str
    timestamp_seconds: Optional[float] = Field(
        default=None,
        description=(
            "Where in the source video this frame was sampled. Null when the "
            "extractor could not probe the duration and fell back to fps "
            "sampling — the frame is still usable."
        ),
    )
    width: int
    height: int
    filename: str


class CoverSelectRequest(BaseModel):
    """选定一帧 → 产出竖版 3:4 + 横版 4:3 两张封面。"""

    frame_resource_id: str = Field(
        description="The candidate frame the user picked (an image resource)"
    )
    publish_task_id: Optional[str] = Field(
        default=None,
        description=(
            "Write the two cover ids back onto this publish task. Optional "
            "because the usual order is cover-first: the compose form picks a "
            "cover before POST /distribution/tasks exists, then passes the two "
            "ids into the create body. Supply it only when editing a task that "
            "has already been created."
        ),
    )


class CoverSelectResponse(BaseModel):
    cover_vertical_resource_id: str
    cover_horizontal_resource_id: str
    publish_task_id: Optional[str] = Field(
        default=None,
        description="Echoed back when the covers were written onto a task.",
    )


__all__ = [
    "CoverCandidateOut",
    "CoverExtractRequest",
    "CoverExtractResponse",
    "CoverSelectRequest",
    "CoverSelectResponse",
]
