"""save_chat_temp_upload registers the resource into the Generated inbox.

The bytes already live under the resource, so this is a row-only registration
(``promoted_resource_id`` = the resource, ``file_path`` = its stored path).
The registration must never be able to fail the upload — the file is already
written and the user is waiting on it — so the failure branch is pinned too,
including the fact that it LOGS (a silent swallow is the defect this guards).
"""

import pytest
from loguru import logger

from app.repositories.generated_media_repository import GeneratedMediaRepository
from app.services.library import chat_upload


class _FakeResourcesService:
    def __init__(self, resource):
        self._resource = resource
        self.calls = []

    async def upload_resource(self, **kwargs):
        self.calls.append(kwargs)
        return self._resource


@pytest.fixture
def wired(monkeypatch):
    """Everything below save_chat_temp_upload stubbed except the registration."""
    resource = {"id": 4242, "file_path": "teams/777/temp/abc.png"}
    svc = _FakeResourcesService(resource)

    async def _scope(*, session_id, user_id):
        return ("team", "777")

    async def _folder(scope_type, scope_id, user_id):
        return "folder-1"

    monkeypatch.setattr(chat_upload, "resolve_chat_scope", _scope)
    monkeypatch.setattr(chat_upload, "_ensure_temp_folder", _folder)
    monkeypatch.setattr(chat_upload, "_resources_service", lambda: svc)

    calls = []

    async def _insert(self, **kwargs):
        calls.append(kwargs)
        return {"id": "9001"}

    monkeypatch.setattr(GeneratedMediaRepository, "insert_registered_resource", _insert)
    return calls


async def _upload(*, session_id="555", mime="image/png", filename="a.png"):
    return await chat_upload.save_chat_temp_upload(
        user_id="u-1",
        session_id=session_id,
        file_bytes=b"x" * 10,
        filename=filename,
        mime=mime,
    )


class TestRegistration:
    async def test_insert_kwargs(self, wired):
        out = await _upload()
        assert out["resource_id"] == "4242"
        assert wired == [
            {
                "scope_id": 777,
                "creator_id": "u-1",
                "resource_id": 4242,
                "file_path": "teams/777/temp/abc.png",
                "mime": "image/png",
                "media_kind": "image",
                "conversation_id": 555,
                "origin_kind": "chat_upload",
            }
        ]

    async def test_video_mime_is_a_video_kind(self, wired):
        await _upload(mime="video/mp4", filename="a.mp4")
        assert wired[0]["media_kind"] == "video"

    async def test_other_mime_is_a_file_kind(self, wired):
        await _upload(mime="application/pdf", filename="a.pdf")
        assert wired[0]["media_kind"] == "file"

    async def test_non_numeric_session_has_no_conversation(self, wired):
        await _upload(session_id="issue-abc")
        assert wired[0]["conversation_id"] is None

    async def test_absent_session_has_no_conversation(self, wired):
        await _upload(session_id=None)
        assert wired[0]["conversation_id"] is None


class TestRegistrationFailureNeverFailsTheUpload:
    async def test_upload_still_succeeds_and_the_failure_is_logged(
        self, monkeypatch, wired
    ):
        async def _boom(self, **kwargs):
            raise RuntimeError("db is down")

        monkeypatch.setattr(
            GeneratedMediaRepository, "insert_registered_resource", _boom
        )
        records: list[str] = []
        sink_id = logger.add(records.append, level="ERROR", format="{message}")
        try:
            out = await _upload()
        finally:
            logger.remove(sink_id)

        assert out["resource_id"] == "4242"
        assert out["file_path"] == "teams/777/temp/abc.png"
        # The resource id has to be in the log line — it is the only handle a
        # human has to reconcile the row the backfill will later create.
        assert any("4242" in r for r in records), records
