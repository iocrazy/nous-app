"""Distribution — 曲库搜索的响应形状。

失败**不走这里**：任何失败都是一个带 ``detail.reason`` 的 HTTP 错误（见
``services/distribution/music_catalog.py`` 的类型化 reason）。一个 200 + 空列表
在面板里与"这个词没有歌"完全无法区分——而实测告诉我们空列表**几乎不会**是后者
（乱码关键词照样回 8 条），所以把失败表达成空列表等于把一次故障说成一个结论。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class MusicTrackOut(BaseModel):
    """曲库里的一首歌。

    ⚠️ ``music_id`` 是上游的 ``id_str``（字符串），不是同一条结果里的 ``id``
    （JSON number，实测超过 2^53，到 JS 就不是那个数了）。发布时用它当身份。
    """

    music_id: str
    title: str
    author: str = ""
    #: 秒（[实测 2026-08-15]）。格式化成 mm:ss 是前端的事。
    duration: int = 0
    #: 使用人数原始整数。「3 万人用过」是格式化出来的。
    user_count: int = 0
    cover_url: str = ""
    #: 试听。上游白送的，缺失是常态。
    play_url: str = ""


class BrowseIdentityOut(BaseModel):
    """这一次是**以谁的身份**在看曲库。

    面板必须把它显示出来。曲库搜索本身与账号无关，但换搜索凭证那一步用的是某
    个已绑账号的会话，而「收藏」这类 tab（Phase 2）**是按账号隔离的**（实测三
    个账号分别拿到 0 / 18 / 9 条收藏）。不显示身份，用户换个目标账号就会看到
    列表悄悄变了却不知道为什么 —— 系统知道、用户看不到，本仓库明令禁止的那一族。
    """

    account_id: str
    username: str
    avatar_url: Optional[str] = None


class MusicSearchResponse(BaseModel):
    platform: str
    keyword: str
    tracks: list[MusicTrackOut] = Field(default_factory=list)
    #: 下一页游标，原样来自上游（各 tab 语义不同，前端只负责回传）。
    cursor: int = 0
    has_more: bool = False
    #: 这一次是不是命中了进程内短 TTL 缓存。纯可观测信息，UI 不依赖它。
    cached: bool = False
    #: 用谁的会话问到的。见 ``BrowseIdentityOut``。
    browsing_as: BrowseIdentityOut


# --- 「选择音乐」 charts cache (P7) --------------------------------------------
#
# Mirrors ``MusicChartsRepository.list_charts`` / ``_track_payload`` and
# ``music_charts.harvest_account_charts`` key for key. Wire test:
# ``tests/api/test_distribution_p7_wire.py``.


class DistributionMusicChartTrack(BaseModel):
    """A cached chart track: the search ``MusicTrackOut`` vocabulary plus its
    ``position``. ``user_count`` is ``None`` when the platform did not say
    (zero is a real value)."""

    music_id: str
    title: str
    author: str
    duration: int
    user_count: Optional[int]
    cover_url: str
    play_url: str
    position: int


class DistributionMusicChart(BaseModel):
    """One cached tab. ``category_kind`` + ``category_id`` together are its
    identity. ``ok=false`` still carries the tracks from the last good read;
    ``fetched_at`` advances on success only, ``checked_at`` on every try."""

    id: str
    category_id: str
    category_kind: str
    category_name: str
    position: int
    ok: bool
    error: str
    cursor: str
    has_more: bool
    fetched_at: Optional[str]
    checked_at: Optional[str]
    tracks: list[DistributionMusicChartTrack]


class DistributionMusicChartsPage(BaseModel):
    charts: list[DistributionMusicChart]
    last_success_at: Optional[str]
    stale: bool
    never_harvested: bool
    ttl_hours: int


class DistributionMusicHarvestResult(BaseModel):
    """``POST /accounts/{id}/music/charts/refresh``. Always 200: a failure is
    ``success=false`` with ``detail.reason`` (``account_busy``,
    ``no_chart_read`` …), never an HTTP error. ``stored`` is the write
    summary (``stored`` / ``kept`` / ``tracks``), empty when nothing was
    written."""

    success: bool
    status: str
    message: Optional[str]
    detail: dict[str, Any]
    stored: dict[str, int]
