"""Distribution — 封面抽帧 / 选帧的请求与响应模型。

两个动作的形状差异很大，所以分成两组：抽帧是**异步**的（返回 task_id，前端
订阅 task_tracking Realtime 拿候选清单），选帧是**同步**的（返回两个 resource
id，前端立刻能用）。理由见 ``app/workflows/cover_frames.py`` 的模块 docstring。

BIGINT id 一律用 str 序列化 —— 与 ``distribution_publish`` 同款，JS 的 2^53
放不下 snowflake。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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
            "How many evenly-spaced frames to sample. Candidates are temporary "
            "base64 previews carried on task_tracking.metadata (they are NOT "
            "resource rows), so the ceiling is what keeps that Realtime row "
            "under its size limit — see MAX_COVER_FRAMES in cover_frames.py."
        ),
    )


class CoverExtractResponse(BaseModel):
    """``task_id`` 同时是 task_tracking 行的主键与 DBOS workflow id。前端订阅
    这一行，成功时 ``metadata.cover_frames.candidates`` 就是候选清单。"""

    task_id: str


class CoverCandidateOut(BaseModel):
    """一个候选帧：一张内联的小预览图 + 它在源视频里的时间点。

    **没有 resource_id**：候选帧不再落成 resources 行（那会把它们混进用户放源
    视频的那个文件夹），改成纯临时物 —— 预览随这份 jsonb 送达，用户挑中后由
    ``/covers/select`` 按 ``timestamp_seconds`` 重抽同一帧再裁。理由与"同一秒
    数必然重抽出同一帧"的实测见
    ``app/services/distribution/cover_frames.py`` 的模块 docstring。

    这个模型目前不出现在任何端点的 response_model 上 —— 候选清单是经
    ``task_tracking.metadata`` 走 Realtime 送达的，不是 HTTP 响应。放在这里是
    为了让那份 jsonb 的形状有一处**声明式**的契约，前端类型照它写，改字段时
    两边能对上。
    """

    index: int = Field(description="Position within this sampling run")
    timestamp_seconds: float = Field(
        description=(
            "Where in the source video this frame was sampled. Required: it is "
            "the only coordinate /covers/select accepts, so a frame without one "
            "cannot be offered at all (the extractor's fps fallback, which "
            "produces untimestamped frames, is rejected upstream with a typed "
            "422 rather than shown as tiles that do nothing when clicked)."
        )
    )
    preview_data_url: str = Field(
        description="`data:image/jpeg;base64,...` — goes straight into <img src>"
    )
    preview_width: int
    preview_height: int


class CoverSelectRequest(BaseModel):
    """选定一帧 → 产出竖版 3:4 + 横版 4:3 两张封面。

    两种形状，**必须且只能给一种**：

    - ``source_resource_id`` + ``timestamp_seconds`` —— 当前的形状。候选帧不
      落库，所以用户挑的是一个时间点，服务端回到源视频重抽那一帧。
    - ``frame_resource_id`` —— 旧形状，只为部署错峰窗口保留（前端与后端是两条
      独立发布链，用户可能卡在"候选已拿到、还没点"的中间）。见
      ``cover_frames.derive_cover_pair_from_frame`` 的 docstring。

    校验放在这里而不是 router：两种形状都缺 / 混着给，都是**用户可达**的坏输入，
    应该是一个说得清的 422，而不是让 router 去 ``if`` 出一个 500。
    """

    source_resource_id: Optional[str] = Field(
        default=None,
        description="The video the frames were sampled from",
    )
    timestamp_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="Which frame, as its offset into that video",
    )
    frame_resource_id: Optional[str] = Field(
        default=None,
        description=(
            "DEPRECATED — a candidate frame that was persisted as an image "
            "resource. Kept only for the frontend/backend deploy-skew window."
        ),
    )
    # 裁切锚点（归一化 0..1，框中心）。缺省居中。工作室里拖动裁切框就是在改它。
    focus_x: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    focus_y: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # 缩放条：1 = 框尽量大；2 = 框边长减半；上限 4。缺省 1。
    zoom: Optional[float] = Field(default=None, ge=1.0, le=4.0)
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

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> "CoverSelectRequest":
        by_timestamp = (
            self.source_resource_id is not None and self.timestamp_seconds is not None
        )
        by_frame = self.frame_resource_id is not None
        if by_timestamp and by_frame:
            raise ValueError(
                "give either source_resource_id + timestamp_seconds or "
                "frame_resource_id, not both"
            )
        if not by_timestamp and not by_frame:
            raise ValueError(
                "source_resource_id + timestamp_seconds are required "
                "(frame_resource_id is the deprecated alternative)"
            )
        return self


class CoverGrabFrameRequest(BaseModel):
    """Grab one frame by hand, to use as a REFERENCE (Cover Studio).

    Deliberately not the same request as ``CoverSelectRequest``: that one
    finishes a cover, this one only collects raw material. Sharing a schema
    would make the two indistinguishable at the boundary, and they differ in
    the one way that matters — where the bytes land.
    """

    source_resource_id: str = Field(..., min_length=1)
    timestamp_seconds: float = Field(..., ge=0)


class CoverGrabFrameResponse(BaseModel):
    """The frame, already durable enough to be handed to the model.

    ``url`` is the ONLY URL shape the image generation bridge accepts as a
    reference; the frontend must pass it through untouched rather than rebuild
    it from ``generated_media_id``.
    """

    generated_media_id: str
    url: str
    timestamp_seconds: float


class CoverGenerateRequest(BaseModel):
    """Dispatch one Cover Studio generation.

    ``stage`` is the whole shape of the flow, so it is a required Literal and
    not an optional flag: stage 1 asks for the 2x2 grid of four drafts, stage 2
    redraws the one the user picked. They build different prompts and stage 2
    is meaningless without ``selected_draft``.
    """

    stage: Literal[1, 2]
    topic: str = Field(..., min_length=1, max_length=200)
    # 画幅。3:4 是竖封面，4:3 是横封面 —— 两个都走 AI（2026-08-26 用户拍板）。
    aspect: Literal["3:4", "4:3"] = "3:4"
    # 风格 = 一个 category='cover' 的 skill 的 slug。内置的 viral-video-cover
    # 走代码里转录好的骨架；其余 skill 走 cover_styles.build_from_skill。
    style: str = Field(default="viral-video-cover", max_length=120)
    # mediahub_models catalog row name; "" = let the catalog pick.
    model: str = ""
    # generated_media reference URLs, in pool order. The server re-checks the
    # cap, so a client bug cannot quietly spend more than nine.
    source_urls: list[str] = Field(default_factory=list, max_length=9)
    quality: str = "high"
    # ⚠️ Default OFF — the two source files disagree and this follows SKILL.md,
    # which forbids them. See cover_prompt.CoverPromptInput.
    allow_small_labels: bool = False
    # Stage 2 only.
    selected_draft: Optional[int] = Field(default=None, ge=1, le=4)
    headline: str = Field(default="", max_length=120)
    # 创作者写给模型的一句话（提示词框）。空串 = 不加。
    instructions: str = Field(default="", max_length=500)
    # 这次生成是为哪个视频做封面 —— 写进 generated_media.params.cover，工作室重新打开时
    # 按它把历史轮次拉回来（轮次不再只活在内存里）。
    source_video_id: str = Field(default="", max_length=32)
    # 阶段二：它精修的是哪张网格（generated_media id），历史里成品才能挂回那一轮。
    grid_gen_id: str = Field(default="", max_length=32)

    @field_validator("topic")
    @classmethod
    def _topic_not_blank(cls, v: str) -> str:
        trimmed = (v or "").strip()
        if not trimmed:
            # ``min_length=1`` inspects the RAW string, so four spaces pass it
            # and then the prompt builder raises deeper in — as a 500.
            raise ValueError("topic cannot be blank")
        return trimmed


class CoverStyleOut(BaseModel):
    slug: str
    name: str
    description: str = ""
    # 这套风格是否要求人物参考图（前端据此做阻断）。
    requires_person: bool = False
    aspects: list[str] = Field(default_factory=lambda: ["3:4", "4:3"])
    builtin: bool = False


class CoverStylesOut(BaseModel):
    styles: list[CoverStyleOut]


class CoverGenerateResponse(BaseModel):
    task_id: str
    # Echoed so the UI can show exactly what the model was asked, which the
    # design puts behind a "What was sent to the model" disclosure. Sending it
    # back beats rebuilding it client-side and hoping the two agree.
    prompt: str
    aspect: str
    reference_count: int


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
    "CoverStyleOut",
    "CoverStylesOut",
]
