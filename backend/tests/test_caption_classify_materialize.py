"""caption_asset / classify_asset workflows read originals via materialize().

Task 2.4c (storage unification final-review wave): both workflows used to
join ``Path(settings.DOWNLOAD_PATH) / file_path`` directly, which breaks for
``sb://`` rows (CaptionService._encode_image_sync does ``Image.open`` on the
joined path). Now the LLM call is wrapped in ``async with
materialize(file_path)`` — the fs row keeps its real path, the sb:// row is
streamed to a temp file first.

Drives the real workflow bodies (``inspect.unwrap`` past @DBOS.workflow —
same approach as test_upload_postprocess_workflow.py) with materialize and
the @DBOS.step calls stubbed, so the tests pin exactly the seam that
changed: the file the vision call receives is the MATERIALIZED path, not a
DOWNLOAD_PATH join of the raw column value.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

_USER = "11111111-1111-1111-1111-111111111111"
_RID = "9000000000000000001"
_SB_PATH = "sb://library/t42/ab/cd/abcdef1234.png"


def _make_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.update_progress = AsyncMock(return_value=None)
    return mgr


def _fake_materialize(seen: dict, local: Path):
    @asynccontextmanager
    async def _materialize(file_path: str):
        seen["file_path"] = file_path
        yield local

    return _materialize


def _resource_repo(resource: dict) -> MagicMock:
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    repo.update_resource = AsyncMock(return_value=dict(resource))
    return repo


async def test_caption_sb_row_calls_llm_with_materialized_path(tmp_path):
    from app.workflows import caption_asset as m

    local = tmp_path / "materialized.png"
    local.write_bytes(b"png-bytes")
    seen: dict = {}
    resource = {"id": _RID, "file_path": _SB_PATH, "file_type": "image"}
    repo = _resource_repo(resource)
    call = AsyncMock(return_value={"en": "an english prompt", "zh": "a zh prompt"})

    with (
        patch.object(m, "materialize", _fake_materialize(seen, local)),
        patch.object(
            m,
            "resolve_caption_provider",
            AsyncMock(
                return_value={
                    "provider_key": "qwen",
                    "provider_config": {},
                    "agent_model": "m",
                    "agent_slug": "caption",
                }
            ),
        ),
        patch.object(m, "call_caption", call),
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=_make_manager(),
        ),
    ):
        result = await inspect.unwrap(m.caption_asset_workflow)(
            resource_id=_RID, user_id=_USER
        )

    assert result["status"] == "ok"
    # materialize was entered with the RAW column value (sb:// shape) …
    assert seen["file_path"] == _SB_PATH
    # … and the vision call received the materialized LOCAL path, never a
    # DOWNLOAD_PATH join of the sb:// value.
    call.assert_awaited_once()
    assert call.await_args.kwargs["abs_path"] == str(local)
    repo.update_resource.assert_awaited_once()
    assert repo.update_resource.await_args.args[1] == {
        "gen_prompt": "an english prompt",
        "gen_prompt_zh": "a zh prompt",
    }


async def test_classify_sb_row_calls_llm_with_materialized_path(tmp_path):
    from app.workflows import classify_asset as m

    local = tmp_path / "materialized.png"
    local.write_bytes(b"png-bytes")
    seen: dict = {}
    resource = {"id": _RID, "file_path": _SB_PATH, "file_type": "image"}
    repo = _resource_repo(resource)
    call = AsyncMock(
        return_value=[
            {"dimension": "style", "group": "Style", "en": "anime", "zh": "动漫"}
        ]
    )

    tags_repo = MagicMock()
    tags_repo.get_or_create_group = AsyncMock(return_value={"id": "g1"})
    tags_repo.get_tag_by_name = AsyncMock(return_value={"id": "t1"})
    tags_repo.add_tag_to_resource = AsyncMock(return_value=None)

    with (
        patch.object(m, "materialize", _fake_materialize(seen, local)),
        patch.object(
            m,
            "resolve_classify_provider",
            AsyncMock(
                return_value={
                    "provider_key": "qwen",
                    "provider_config": {},
                    "agent_model": "m",
                    "agent_slug": "classify",
                }
            ),
        ),
        patch.object(m, "call_classify", call),
        patch(
            "app.repositories.resources_repository.ResourcesRepository",
            return_value=repo,
        ),
        patch(
            "app.repositories.tags_repository.get_tags_repository",
            return_value=tags_repo,
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=_make_manager(),
        ),
    ):
        result = await inspect.unwrap(m.classify_asset_workflow)(
            resource_id=_RID, user_id=_USER
        )

    assert result["status"] == "ok"
    assert result["tags_added"] == 1
    assert seen["file_path"] == _SB_PATH
    call.assert_awaited_once()
    assert call.await_args.kwargs["abs_path"] == str(local)
