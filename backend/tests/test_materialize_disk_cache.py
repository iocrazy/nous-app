"""materialize() 的 S3 读通磁盘缓存(spec 2026-08-02-s3-materialize-disk-cache)。

followup 链各阶段独立 materialize 同一对象,无缓存时同链重复全量拉 3-4 次。
缓存启用后:miss 拉一次入缓存(0444,原子入位),命中零拉取刷 mtime,退出不删;
禁用(默认)保持 temp 下载用完即删的旧行为;超上限按 mtime LRU 淘汰(30 分钟
内新文件豁免);下载失败清 tmp 上抛。
"""

import os
import time

import pytest

import app.services.library.media_storage as media_storage
from app.core.config import settings
from app.services.library.media_storage import materialize


class FakeStore:
    calls = 0

    def __init__(self, bucket):
        self.bucket = bucket

    async def get_stream(self, key):
        FakeStore.calls += 1
        yield b"chunk1-"
        yield b"chunk2"


class BoomStore:
    def __init__(self, bucket):
        pass

    async def get_stream(self, key):
        yield b"partial"
        raise RuntimeError("storage-api died mid-stream")


SB = "sb://library/t5/ab/cd/abcd1234.mp4"


@pytest.fixture(autouse=True)
def _reset_calls():
    FakeStore.calls = 0
    yield


@pytest.mark.asyncio
async def test_disabled_keeps_temp_behavior(monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_S3_CACHE_DIR", "")
    monkeypatch.setattr(media_storage, "ObjectStore", FakeStore)

    async with materialize(SB) as p:
        assert p.read_bytes() == b"chunk1-chunk2"
        kept = p
    assert not kept.exists()  # 旧行为:退出即删


@pytest.mark.asyncio
async def test_miss_downloads_once_and_caches_readonly(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_S3_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(media_storage, "ObjectStore", FakeStore)

    async with materialize(SB) as p:
        assert p.read_bytes() == b"chunk1-chunk2"
        assert p.parent == tmp_path
        assert p.suffix == ".mp4"
        cached = p
    assert cached.exists()  # 退出不删
    assert (cached.stat().st_mode & 0o777) == 0o444
    assert FakeStore.calls == 1
    assert not list(tmp_path.glob(".tmp-*"))  # 无 tmp 残留


@pytest.mark.asyncio
async def test_hit_skips_store_and_bumps_mtime(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_S3_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(media_storage, "ObjectStore", FakeStore)

    async with materialize(SB) as p1:
        pass
    old = time.time() - 3600
    os.utime(p1, (old, old))

    async with materialize(SB) as p2:
        assert p2 == p1
        assert p2.read_bytes() == b"chunk1-chunk2"
    assert FakeStore.calls == 1  # 第二次零拉取
    assert p1.stat().st_mtime > old + 1800  # mtime 被刷新(LRU 信号)


@pytest.mark.asyncio
async def test_download_failure_cleans_tmp_and_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MEDIA_S3_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(media_storage, "ObjectStore", BoomStore)

    with pytest.raises(RuntimeError, match="mid-stream"):
        async with materialize(SB):
            pass
    assert list(tmp_path.iterdir()) == []  # 无半文件、无 tmp 残留


def test_evict_removes_oldest_beyond_cap(tmp_path):
    from app.services.library.media_storage import _evict_s3_cache

    old_t = time.time() - 7200
    for i, name in enumerate(["a.mp4", "b.mp4", "c.mp4"]):
        f = tmp_path / name
        f.write_bytes(b"x" * 1024)
        os.utime(f, (old_t + i, old_t + i))  # a 最旧
    # 上限 ~2KB:总量 3KB,需删 1 个 → 最旧的 a
    _evict_s3_cache(str(tmp_path), max_gb=2 * 1024 / (1024**3))

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["b.mp4", "c.mp4"]


@pytest.mark.asyncio
async def test_unwritable_cache_dir_falls_back_to_temp(monkeypatch, tmp_path):
    """缓存目录存在但不可写(docker 自动重建 bind source 为 root:root 0755 的
    经典场景)→ 必须降级到 temp,而不是让转码/缩略图/AI 全线硬失败。"""
    ro = tmp_path / "ro"
    ro.mkdir(mode=0o555)
    monkeypatch.setattr(settings, "MEDIA_S3_CACHE_DIR", str(ro))
    monkeypatch.setattr(media_storage, "ObjectStore", FakeStore)

    async with materialize(SB) as p:
        assert p.read_bytes() == b"chunk1-chunk2"
        assert p.parent != ro  # 走的是 temp
        kept = p
    assert not kept.exists()
    assert list(ro.iterdir()) == []


@pytest.mark.asyncio
async def test_cache_write_error_falls_back_to_temp(monkeypatch, tmp_path):
    """缓存写入过程报 OSError(磁盘满/目录中途被删)→ 降级 temp 继续可用。"""
    monkeypatch.setattr(settings, "MEDIA_S3_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(media_storage, "ObjectStore", FakeStore)

    real_replace = os.replace

    def boom_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", boom_replace)
    async with materialize(SB) as p:
        assert p.read_bytes() == b"chunk1-chunk2"  # 内容照常拿到
        assert p.parent != tmp_path
    monkeypatch.setattr(os, "replace", real_replace)
    assert not list(tmp_path.glob(".tmp-*"))  # tmp 已清理


def test_evict_spares_fresh_files(tmp_path):
    """30 分钟内的新文件豁免:刚入位、调用方尚未 open 的窗口不被并发淘汰误伤。"""
    from app.services.library.media_storage import _evict_s3_cache

    fresh = tmp_path / "fresh.mp4"
    fresh.write_bytes(b"x" * 4096)  # 超上限但新鲜
    _evict_s3_cache(str(tmp_path), max_gb=1024 / (1024**3))

    assert fresh.exists()
