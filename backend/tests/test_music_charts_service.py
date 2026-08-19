"""缓存「选择音乐」榜单：写入决策、失效判据、种子图，以及采集编排。

这一层最要紧的两件事都不是happy path：

1. **一次失败的采集不能毁掉上一次成功的**（`plan_chart_write` → `keep`）；
2. **一次部分失败的采集，读到的那几个榜仍然要存下来** —— 否则第四个 tab 超时
   会让前三个真实榜单一起丢掉，把局部故障放大成全部故障。
"""

from __future__ import annotations

import datetime

import pytest

from app.repositories.music_charts_repository import (
    WRITE_KEEP,
    WRITE_REPLACE,
    WRITE_SKIP,
    plan_chart_write,
)
from app.services.distribution.music_charts import (
    DEFAULT_TTL_HOURS,
    REASON_ACCOUNT_BUSY,
    REASON_ACCOUNT_MISSING,
    SEED_SIDE_PX,
    is_stale,
    seed_media_item,
    seed_png,
)

pytestmark = pytest.mark.unit


# ── 写入决策 ────────────────────────────────────────────────────────────


def test_a_successful_read_replaces_what_was_stored():
    assert plan_chart_write(
        {"category_kind": "rank", "category_id": "7", "ok": True}
    ) == (WRITE_REPLACE)


def test_a_successful_read_with_no_tracks_still_replaces():
    """空榜单也是真相。

    实测：没收藏过歌的账号，收藏接口回 137 字节、连 songs 键都没有。把"空"当
    成"没读到"，会让空收藏夹永远显示上一次的残留。
    """
    chart = {"category_kind": "fav", "category_id": "1", "ok": True, "tracks": []}
    assert plan_chart_write(chart) == WRITE_REPLACE


def test_a_failed_read_keeps_what_was_stored():
    """**这条是本文件存在的主要理由。**

    把它改成无条件 DELETE+INSERT，一次网络抖动就会把某个 tab 清空，而且要等到
    下一次定时采集才会恢复。昨天的榜单远好过没有榜单 —— 只要界面能看出它是
    昨天的（这就是 fetched_at 与 checked_at 分开的原因）。
    """
    assert plan_chart_write(
        {"category_kind": "rank", "category_id": "7", "ok": False}
    ) == (WRITE_KEEP)


def test_half_an_identity_is_skipped_rather_than_stored():
    """键是 kind + id 两列合起来。少一半就是一行谁也匹配不上的死行，每跑一次
    多漏一行，永远清不掉。"""
    assert (
        plan_chart_write({"category_kind": "", "category_id": "1", "ok": True})
        == WRITE_SKIP
    )
    assert plan_chart_write(
        {"category_kind": "rank", "category_id": "", "ok": True}
    ) == (WRITE_SKIP)
    assert plan_chart_write({}) == WRITE_SKIP


# ── 失效判据 ────────────────────────────────────────────────────────────


def test_never_harvested_is_stale():
    """空缓存必须是"该采了"，否则定时任务永远不会跑第一次。"""
    assert is_stale(None) is True


def test_a_fresh_harvest_is_not_stale():
    now = datetime.datetime.now(datetime.timezone.utc)
    assert is_stale(now - datetime.timedelta(hours=1)) is False


def test_an_old_harvest_is_stale():
    now = datetime.datetime.now(datetime.timezone.utc)
    assert is_stale(now - datetime.timedelta(hours=DEFAULT_TTL_HOURS + 1)) is True


def test_a_naive_timestamp_counts_as_stale_not_fresh():
    """两个方向的代价不对称：判错成"该采"只多跑一次；判错成"还新鲜"是一个
    永远不刷新、也永远不说为什么的缓存。"""
    naive = datetime.datetime.now() - datetime.timedelta(hours=DEFAULT_TTL_HOURS + 1)
    assert is_stale(naive) is True


# ── 种子图 ──────────────────────────────────────────────────────────────


def test_the_seed_is_a_real_png_of_the_measured_size():
    """不是 1×1：平台会拒绝退化的图片，而被拒绝的上传会表现为"面板没打开"——
    一个在我们所有日志里都看不出成因的失败。"""
    data = seed_png()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"

    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (SEED_SIDE_PX, SEED_SIDE_PX)


def test_the_seed_is_deterministic_and_carries_no_user_data():
    """它是代码里画出来的一块纯色。这一条钉住的是"没有借用用户素材"——
    借用会把用户自己的内容，每天，因为一个与内容无关的理由，送进一个真实平台
    账号的草稿箱。"""
    assert seed_png() == seed_png()


def test_the_seed_url_points_at_the_backend_on_the_docker_network():
    item = seed_media_item("http://nous-backend:8080/")
    assert item["url"] == (
        "http://nous-backend:8080/api/v1/distribution/music/seed.png"
    )
    assert item["kind"] == "image"


# ── 采集编排 ────────────────────────────────────────────────────────────


class _FakeAccounts:
    def __init__(self, account: dict | None) -> None:
        self._account = account
        self.written: list[str] = []

    async def get_with_session(self, _account_id: int):
        return None if self._account is None else dict(self._account)

    async def update_session_state(self, _account_id: int, state: str) -> None:
        self.written.append(state)


class _FakeRepo:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def replace_charts(self, account_id, platform, charts):
        self.calls.append((account_id, platform, list(charts)))
        return {"stored": sum(1 for c in charts if c.get("ok")), "kept": 0, "tracks": 0}


async def test_a_missing_account_is_a_typed_refusal_not_an_exception(monkeypatch):
    from app.services.distribution import music_charts as svc

    monkeypatch.setattr(
        "app.repositories.social_accounts_repository.SocialAccountsRepository",
        lambda: _FakeAccounts(None),
    )
    out = await svc.harvest_account_charts(1, base_url="http://x")
    assert out["success"] is False
    assert out["detail"]["reason"] == REASON_ACCOUNT_MISSING


def test_the_busy_reason_is_its_own_code():
    """账号忙不是错误，是"过一分钟再来"。前端按 code 出文案，绝不正则匹配
    英文（CLAUDE.md「触发路径必须类型化失败回显」）。"""
    assert REASON_ACCOUNT_BUSY == "account_busy"
    assert REASON_ACCOUNT_MISSING != REASON_ACCOUNT_BUSY
