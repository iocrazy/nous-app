"""cover_frames workflow —— 编排层测试（``inspect.unwrap`` 穿过 @DBOS.workflow）。

只关心三件**必须成立**的事，其余（下载、ffmpeg、裁切）归 service 层的
``tests/test_cover_frames.py``：

1. **失败 raise，不 return failed dict**（路线 C 纪律 4）。返回 dict 会被 DBOS
   判成 SUCCESS，trigger 于是把 task_tracking 标 completed —— 用户看到的是
   "任务完成了但一张候选帧都没有"。
2. **候选清单走 ``complete(metadata_patch=...)``，不走 update_progress**。后者
   对同一 task 有 1 write/sec 节流且会**整条丢弃**（含 metadata_patch），清单丢
   一次这次抽帧就白跑了。同时 metadata 里只能有 resource **id** —— 塞 base64
   会经 Realtime 推给每个订阅者。
3. **失败也要有类型化回显**：``error_status`` 让前端能区分"这个视频读不了"
   （422/404）与"超时了，重试可能有用"（504）。silent no-op 不可接受。
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.workflows.cover_frames as m
from app.services.distribution.cover_frames import CoverFrameError

pytestmark = pytest.mark.unit

WF_ID = "wf-cover-1"
USER = "11111111-1111-1111-1111-111111111111"


class _Manager:
    """记录 manager API 的每一次调用 —— 断言的是"走了 manager"，不是"PATCH 了
    某一列"（phase/status/progress 归 trigger，业务代码不许碰）。"""

    def __init__(self) -> None:
        self.completed: List[Dict[str, Any]] = []
        self.failed: List[Dict[str, Any]] = []
        self.progress: List[Dict[str, Any]] = []

    async def complete(self, task_id, *, subtitle=None, metadata_patch=None):
        self.completed.append(
            {"task_id": task_id, "subtitle": subtitle, "metadata": metadata_patch}
        )

    async def fail(self, task_id, error_msg, *, error_code=None, metadata_patch=None):
        self.failed.append(
            {"task_id": task_id, "error": error_msg, "metadata": metadata_patch}
        )

    async def update_progress(self, *a, **kw):
        self.progress.append({"args": a, "kwargs": kw})

    async def start(self, task_id, *, user_id=None, task_type="download"):
        self.progress.append({"start": task_id, "user_id": user_id})


async def _run(
    *,
    step_result: Optional[Dict[str, Any]] = None,
    step_error: Optional[BaseException] = None,
    num_frames: int = m.DEFAULT_COVER_FRAMES,
) -> Tuple[Optional[dict], Optional[BaseException], _Manager, AsyncMock]:
    manager = _Manager()
    dbos = MagicMock()
    dbos.workflow_id = WF_ID
    step = AsyncMock(return_value=step_result, side_effect=step_error)

    with (
        patch.object(m, "DBOS", dbos),
        patch.object(m, "mark_cover_frames_processing_step", AsyncMock()),
        patch.object(m, "extract_cover_frames_step", step),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=manager,
        ),
    ):
        try:
            result = await inspect.unwrap(m.cover_frames_workflow)(
                source_resource_id="500", user_id=USER, num_frames=num_frames
            )
            error = None
        except BaseException as exc:  # noqa: BLE001 — raise 与否正是被测行为
            result, error = None, exc
    return result, error, manager, step


def _payload(count: int = 3) -> Dict[str, Any]:
    return {
        "source_resource_id": "500",
        "duration_seconds": 30.0,
        "candidates": [
            {
                "resource_id": f"9999000000000{i:03d}",
                "timestamp_seconds": float(i),
                "width": 1080,
                "height": 1920,
                "filename": f"cover-frame-{i}.jpg",
            }
            for i in range(count)
        ],
    }


# ============================================================
# 成功路径
# ============================================================


async def test_success_completes_the_task_with_the_candidate_list_in_metadata() -> None:
    result, error, manager, _ = await _run(step_result=_payload(3))

    assert error is None
    assert result == {
        "status": "completed",
        "source_resource_id": "500",
        "frame_count": 3,
    }
    assert len(manager.completed) == 1
    assert manager.completed[0]["metadata"] == {"cover_frames": _payload(3)}
    assert manager.failed == []


async def test_candidate_metadata_carries_ids_only_never_image_bytes() -> None:
    """metadata 经 Realtime 推给每个订阅者。6 张 1080 宽 JPEG 的 base64 约
    1.5 MB —— 那是给实时通道灌洪水，所以清单里只能有 resource id。"""
    _, _, manager, _ = await _run(step_result=_payload(6))

    blob = repr(manager.completed[0]["metadata"])
    assert "base64" not in blob
    assert "data:image" not in blob
    for candidate in manager.completed[0]["metadata"]["cover_frames"]["candidates"]:
        assert candidate["resource_id"]


async def test_candidate_list_goes_through_complete_not_update_progress() -> None:
    """``update_progress`` 对同一 task 有 1 write/sec 节流且会整条丢弃（含
    metadata_patch）。清单丢一次这次抽帧就白跑了，所以必须搭 complete 走。"""
    _, _, manager, _ = await _run(step_result=_payload(2))

    assert manager.completed[0]["metadata"] is not None
    assert all("metadata_patch" not in p.get("kwargs", {}) for p in manager.progress)


@pytest.mark.parametrize(
    "count,expected", [(1, "1 cover frame ready"), (3, "3 cover frames ready")]
)
async def test_subtitle_is_singular_for_a_single_frame(
    count: int, expected: str
) -> None:
    _, _, manager, _ = await _run(step_result=_payload(count))
    assert manager.completed[0]["subtitle"] == expected


async def test_empty_candidate_list_still_reports_a_count_of_zero() -> None:
    """service 层不会产出这种 payload（零候选是 422），但 workflow 不该在
    ``len(None)`` 上炸 —— 编排层对上游形状要有最低限度的容错。"""
    _, error, manager, _ = await _run(
        step_result={"source_resource_id": "500", "candidates": None}
    )
    assert error is None
    assert manager.completed[0]["subtitle"] == "0 cover frames ready"


@pytest.mark.parametrize(
    "requested,expected", [(0, 1), (-1, 1), (6, 6), (999, m.MAX_COVER_FRAMES)]
)
async def test_frame_count_is_clamped_before_the_step_runs(
    requested: int, expected: int
) -> None:
    """DBOS 的 workflow input 一旦冻结就改不了，所以钳制必须发生在派给 step
    之前 —— 一次恢复重放不能拿着 999 重跑。"""
    _, _, _, step = await _run(step_result=_payload(1), num_frames=requested)
    assert step.await_args.args[3] == expected


# ============================================================
# 失败路径 —— 路线 C 纪律 4
# ============================================================


async def test_extraction_failure_fails_the_task_and_reraises() -> None:
    """返回 failed dict 会被 DBOS 判成 SUCCESS，trigger 于是把 task_tracking
    标 completed，而业务字段（subtitle / metadata）已经写了失败 —— 典型表现就是
    UI 上"phase=completed 但一张候选帧都没有"。所以必须 raise。
    """
    _, error, manager, _ = await _run(
        step_error=CoverFrameError(status_code=422, detail="video is unreadable")
    )

    assert isinstance(error, CoverFrameError)
    assert manager.completed == []
    assert len(manager.failed) == 1
    assert "video is unreadable" in manager.failed[0]["error"]


@pytest.mark.parametrize("status", [404, 422, 504])
async def test_failure_metadata_carries_the_status_code_for_the_ui(
    status: int,
) -> None:
    """前端要据此区分"这个视频读不了"（404/422，重试无意义）与"超时了"
    （504，重试可能有用）。只留一句 error 字符串等于让 UI 去猜。"""
    _, _, manager, _ = await _run(
        step_error=CoverFrameError(status_code=status, detail="nope")
    )

    patch_ = manager.failed[0]["metadata"]["cover_frames"]
    assert patch_["error_status"] == status
    assert patch_["error"] == "nope"
    assert patch_["candidates"] == []
    assert patch_["source_resource_id"] == "500"


async def test_unexpected_exception_propagates_without_being_swallowed() -> None:
    """非 CoverFrameError（真 bug / 基建抖动）不该被吞成完成态。当前实现只
    catch CoverFrameError，所以它会直接冒出去让 DBOS 落 ERROR —— 这条测试守的
    是"不要有一天给它加一个 bare except"。
    """
    _, error, manager, _ = await _run(step_error=RuntimeError("db down"))

    assert isinstance(error, RuntimeError)
    assert manager.completed == []


# ============================================================
# processing 标记 step
# ============================================================


async def test_processing_step_passes_user_id_through() -> None:
    """``start()`` 的自愈建行会撞 ``task_tracking.user_id`` 的 UUID NOT NULL ——
    user_id 不透传的话，任务行在自愈路径上根本建不出来。"""
    manager = _Manager()
    with patch(
        "app.services.infra.unified_task_manager.get_task_manager",
        return_value=manager,
    ):
        await inspect.unwrap(m.mark_cover_frames_processing_step)(WF_ID, USER)

    assert manager.progress == [{"start": WF_ID, "user_id": USER}]


async def test_processing_step_failure_does_not_abort_the_workflow() -> None:
    """标记 processing 是 best-effort：task_tracking 抖一下不该让一次本来能成
    的抽帧失败（与 session_login 链同款）。"""
    manager = MagicMock()
    manager.start = AsyncMock(side_effect=RuntimeError("tracking unavailable"))
    with patch(
        "app.services.infra.unified_task_manager.get_task_manager",
        return_value=manager,
    ):
        await inspect.unwrap(m.mark_cover_frames_processing_step)(WF_ID, USER)


# ============================================================
# 抽帧 step —— heartbeat 与临时文件
# ============================================================


async def test_extraction_step_heartbeats_while_the_download_runs() -> None:
    """这一步里最长的是 materialize 的下载，期间没有任何进度信号 —— 没有
    heartbeat，stall detector 会把安静的任务判成 lost 然后重来一遍。"""
    entered: List[str] = []

    class _Beat:
        def __init__(self, *, workflow_id: str, **kw) -> None:
            self.workflow_id = workflow_id

        async def __aenter__(self):
            entered.append(self.workflow_id)
            return self

        async def __aexit__(self, *exc):
            return False

    async def fake_extract(**kw):
        assert entered == [WF_ID], "抽帧必须发生在 heartbeat 生效之后"

        class _Out:
            candidates = ()
            duration_seconds = None

            def as_dict(self):
                return {"candidates": []}

        return _Out()

    with (
        patch(
            "app.services.workflow_heartbeat.async_heartbeat_loop",
            _Beat,
        ),
        patch(
            "app.services.distribution.cover_frames.extract_cover_candidates",
            fake_extract,
        ),
    ):
        out = await inspect.unwrap(m.extract_cover_frames_step)(WF_ID, "500", USER, 3)

    assert out == {"candidates": []}
    assert entered == [WF_ID]


async def test_step_failure_leaves_no_temp_files_behind(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """workflow 体内抛异常这条路径同样要走到 service 那三个 ``finally``。

    §7.6 的守则不是"service 层测过就行"—— workflow 是真实的调用者，heartbeat
    的 async context manager 又多包了一层，包错了（比如吞掉 CancelledError）
    就会让清理跳票。这里跑真的 service，只打桩 ffmpeg 与对象存储。
    """
    import tempfile

    from app.core.config import settings
    from app.services.distribution import cover_frames as cf
    from app.services.library import media_storage

    scratch = tmp_path / "systmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path / "dl"))
    monkeypatch.setattr(settings, "MEDIA_S3_CACHE_DIR", "")

    class _Store:
        def __init__(self, bucket: str) -> None:
            self.bucket = bucket

        async def get_stream(self, key: str):
            yield b"video-bytes" * 128

    class _Repo:
        async def get_resource_by_id(self, rid):
            return {
                "id": "500",
                "file_path": "sb://library/t1/ab/cd/abcdef.mp4",
                "file_type": "video",
                "mime_type": "video/mp4",
                "filename": "clip.mp4",
            }

        async def get_first_resource_item(self, rid):
            return {"scope_id": "1", "folder_id": None, "library_id": None}

    staged: List[str] = []

    async def exploding_extract(path, **kw):
        staged.append(path)
        raise RuntimeError("ffmpeg wrapper blew up")

    monkeypatch.setattr(media_storage, "ObjectStore", _Store)
    monkeypatch.setattr(cf, "extract_frames", exploding_extract)
    monkeypatch.setattr(
        "app.repositories.resources_repository.ResourcesRepository", lambda: _Repo()
    )

    with pytest.raises(RuntimeError):
        await inspect.unwrap(m.extract_cover_frames_step)(WF_ID, "500", USER, 3)

    # 先证明下载真的落了一个临时文件到我们盯着的目录，否则"目录是空的"是废话。
    from pathlib import Path as _Path

    assert staged and _Path(staged[0]).parent == scratch
    assert list(scratch.iterdir()) == []
