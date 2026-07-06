"""Sideload import (million-files P2).

Covers the inbox-escape defenses (name gate + resolved-path re-check +
symlink refusal), the scan→manifest→batch pipeline, dedup link vs. new-file
registration, replay tolerance (missing source = skipped), and the router's
dispatch contract (flat envelope, pre-created task row, workflow id shape).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.workflows.sideload import (
    SIDELOAD_BATCH_SIZE,
    _manifest_path,
    sideload_batch_step,
    sideload_scan_step,
)


@pytest.fixture()
def inbox(tmp_path):
    """A fake library volume with a sideload inbox batch."""
    with patch("app.workflows.sideload.settings") as s:
        s.DOWNLOAD_PATH = str(tmp_path)
        batch = tmp_path / "sideload-inbox" / "eagle-batch"
        (batch / "sub").mkdir(parents=True)
        (batch / "a.mp4").write_bytes(b"video-bytes")
        (batch / "sub" / "b.png").write_bytes(b"png-bytes")
        (batch / ".hidden").write_bytes(b"dot")  # dotfiles skipped
        yield tmp_path


def _scan(inbox_rel: str, task_id: str):
    return sideload_scan_step.__wrapped__(inbox_rel, task_id)


def _batch(task_id: str, **over):
    kwargs = dict(
        task_id=task_id,
        offset=0,
        limit=SIDELOAD_BATCH_SIZE,
        user_id="u1",
        scope_id="42",
        folder_id=None,
        library_id=None,
        mode="move",
    )
    kwargs.update(over)
    return sideload_batch_step.__wrapped__(**kwargs)


# ─── Scan: manifest + escape defenses ────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_writes_manifest_and_counts(inbox):
    total = await _scan("eagle-batch", "t1")
    assert total == 2  # dotfile excluded
    lines = _manifest_path("t1").read_text().strip().split("\n")
    rels = {json.loads(line)["rel"] for line in lines}
    assert rels == {"eagle-batch/a.mp4", "eagle-batch/sub/b.png"}


@pytest.mark.asyncio
async def test_scan_rejects_escape_and_missing(inbox):
    with pytest.raises(ValueError):
        await _scan("../outside", "t2")
    with pytest.raises(ValueError):
        await _scan("no-such-batch", "t3")


@pytest.mark.asyncio
async def test_scan_skips_symlinks_pointing_anywhere(inbox):
    secret = inbox / "secret.txt"
    secret.write_bytes(b"secret")
    os.symlink(secret, inbox / "sideload-inbox" / "eagle-batch" / "leak.txt")
    total = await _scan("eagle-batch", "t4")
    assert total == 2  # the symlink never enters the manifest


# ─── Batch: register / dedup-link / replay tolerance ─────────────────────────


def _repo_mock(existing_by_hash=None):
    repo = MagicMock()
    repo.find_by_hashes = AsyncMock(return_value=existing_by_hash or {})
    repo.find_resource_item = AsyncMock(return_value=None)
    repo.create_resource = AsyncMock(
        side_effect=lambda data: {"id": f"rid-{data['filename']}"}
    )
    repo.update_resource = AsyncMock(return_value={})
    repo.create_version = AsyncMock(return_value={})
    repo.create_resource_item = AsyncMock(return_value={})
    return repo


@pytest.mark.asyncio
async def test_batch_registers_new_files_into_upload_layout(inbox):
    await _scan("eagle-batch", "t5")
    repo = _repo_mock()
    with patch(
        "app.repositories.resources_repository.ResourcesRepository", return_value=repo
    ):
        counters = await _batch("t5")

    assert counters == {
        "processed": 2,
        "created": 2,
        "linked": 0,
        "skipped": 0,
        "errors": 0,
    }
    # Files moved out of the inbox into teams/{scope}/uploads/{rid}/v1/.
    assert not (inbox / "sideload-inbox" / "eagle-batch" / "a.mp4").exists()
    moved = inbox / "teams" / "42" / "uploads" / "rid-a.mp4" / "v1" / "a.mp4"
    assert moved.exists()
    # file_path column points at the standard layout.
    rel_paths = [c.args[1]["file_path"] for c in repo.update_resource.await_args_list]
    assert "teams/42/uploads/rid-a.mp4/v1/a.mp4" in rel_paths
    # Version + item rows for each new resource; hash recorded.
    assert repo.create_version.await_count == 2
    assert repo.create_resource_item.await_count == 2
    created = repo.create_resource.await_args_list[0].args[0]
    assert created["file_hash"] == hashlib.sha256(b"video-bytes").hexdigest()


@pytest.mark.asyncio
async def test_batch_links_duplicates_zero_copy(inbox):
    await _scan("eagle-batch", "t6")
    dup_hash = hashlib.sha256(b"video-bytes").hexdigest()
    repo = _repo_mock(existing_by_hash={dup_hash: {"id": "999"}})
    with patch(
        "app.repositories.resources_repository.ResourcesRepository", return_value=repo
    ):
        counters = await _batch("t6")

    assert counters["linked"] == 1
    assert counters["created"] == 1  # b.png is still new
    # The duplicate was linked, not re-created, and its inbox copy removed
    # (move mode drains the inbox even for dups).
    link_calls = [c.args[0] for c in repo.create_resource_item.await_args_list]
    assert any(c["resource_id"] == "999" for c in link_calls)
    assert not (inbox / "sideload-inbox" / "eagle-batch" / "a.mp4").exists()


@pytest.mark.asyncio
async def test_batch_register_mode_keeps_files_in_place(inbox):
    await _scan("eagle-batch", "t7")
    repo = _repo_mock()
    with patch(
        "app.repositories.resources_repository.ResourcesRepository", return_value=repo
    ):
        counters = await _batch("t7", mode="register")

    assert counters["created"] == 2
    assert (inbox / "sideload-inbox" / "eagle-batch" / "a.mp4").exists()
    rel_paths = [c.args[1]["file_path"] for c in repo.update_resource.await_args_list]
    assert "sideload-inbox/eagle-batch/a.mp4" in rel_paths


@pytest.mark.asyncio
async def test_batch_replay_counts_missing_sources_as_skipped(inbox):
    """A crashed-then-replayed batch tolerates files the first attempt
    already moved out of the inbox."""
    await _scan("eagle-batch", "t8")
    (inbox / "sideload-inbox" / "eagle-batch" / "a.mp4").unlink()
    repo = _repo_mock()
    with patch(
        "app.repositories.resources_repository.ResourcesRepository", return_value=repo
    ):
        counters = await _batch("t8")

    assert counters["skipped"] == 1
    assert counters["created"] == 1
    assert counters["errors"] == 0


# ─── Router dispatch contract ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sideload_endpoint_flat_envelope_and_precreated_task(inbox):
    from app.api.resources_upload_router import sideload_import

    auth = MagicMock()
    auth.user_id = "u1"
    mgr = MagicMock()
    mgr.create = AsyncMock(return_value="task-uuid")
    start = AsyncMock()

    with (
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=mgr,
        ),
        patch("app.services.infra.dbos_orchestrator.start_workflow_routed", new=start),
    ):
        out = await sideload_import(
            auth,
            inbox_path="eagle-batch",
            scope_id="42",
            folder_id=None,
            library_id=None,
            mode="move",
            _guard=None,
        )

    assert out == {"success": True, "task_id": "task-uuid"}
    # Pre-created row carries the SAME workflow id the dispatch uses
    # (lifecycle trigger correlation).
    wf_id = mgr.create.await_args.kwargs["dbos_workflow_id"]
    assert wf_id.startswith("sideload-")
    assert start.await_args.kwargs["workflow_id"] == wf_id
    assert start.await_args.kwargs["dbos_workflow_kwargs"]["task_id"] == "task-uuid"


@pytest.mark.asyncio
async def test_sideload_endpoint_rejects_bad_names(inbox):
    from app.api.resources_upload_router import sideload_import

    auth = MagicMock()
    auth.user_id = "u1"
    for bad in ("../etc", "a/b", ".hidden", "x" * 200):
        with pytest.raises(HTTPException) as exc:
            await sideload_import(
                auth,
                inbox_path=bad,
                scope_id="42",
                folder_id=None,
                library_id=None,
                mode="move",
                _guard=None,
            )
        assert exc.value.status_code in (400, 404)


def test_sideload_workflow_registered_in_dispatch_bundle():
    import app.workflows._dispatch_bundle as bundle

    assert hasattr(bundle, "sideload_workflow")
