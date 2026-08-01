"""storage_audit:key 收集(UNION 去重只收 sb://)与探测(missing/errors/截断)。"""

import pytest


@pytest.mark.asyncio
async def test_collect_keys_union_dedup(monkeypatch):
    from app.workflows import storage_audit as sa

    async def fake_fetch_all(sql, params=None):
        return [
            {
                "key": "sb://library/t5/aa/bb/v.mp4",
                "kind": "video",
                "media_id": 1,
                "resource_id": None,
            },
            {
                "key": "sb://library/t5/aa/bb/v.mp4",
                "kind": "version_file",
                "media_id": None,
                "resource_id": 9,
            },  # 同 key 不同来源 → 去重保留一条
            {
                "key": "sb://library/derived/9/t.webp",
                "kind": "thumbnail",
                "media_id": None,
                "resource_id": 9,
            },
        ]

    monkeypatch.setattr(sa.db_engine, "fetch_all", fake_fetch_all)
    rows = await sa.collect_audit_keys_step()
    keys = [r["key"] for r in rows]
    assert keys == ["t5/aa/bb/v.mp4", "derived/9/t.webp"]  # 去前缀 + 去重


@pytest.mark.asyncio
async def test_probe_missing_and_errors(monkeypatch):
    from app.workflows import storage_audit as sa

    class FakeStore:
        async def exists(self, key):
            if key == "gone.jpg":
                return False
            if key == "boom.jpg":
                raise RuntimeError("storage down")
            return True

    rows = [
        {"key": "ok.mp4", "kind": "video", "media_id": 1, "resource_id": None},
        {"key": "gone.jpg", "kind": "cover", "media_id": 2, "resource_id": None},
        {"key": "boom.jpg", "kind": "thumbnail", "media_id": None, "resource_id": 3},
    ]
    missing, errors = await sa._probe_keys(FakeStore(), rows, chunk_size=2)
    assert [m["key"] for m in missing] == ["gone.jpg"]
    assert errors == 1  # 异常按不确定计 errors,不进 missing


def test_missing_truncation():
    from app.workflows.storage_audit import _cap_missing

    missing = [
        {"key": f"k{i}", "kind": "video", "media_id": i, "resource_id": None}
        for i in range(600)
    ]
    capped, truncated = _cap_missing(missing)
    assert len(capped) == 500 and truncated is True
    capped2, truncated2 = _cap_missing(missing[:10])
    assert len(capped2) == 10 and truncated2 is False
