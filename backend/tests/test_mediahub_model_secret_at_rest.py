"""Phase 1b — mediahub_models.api_key encrypted at rest, repo-transparent.

create/update bind enc:v1: ciphertext (never plaintext); _row-based reads
(get_by_name / list_all / RETURNING echoes) reveal back to plaintext so the
prober / admin router / provider resolution are untouched. Fail-closed
writes when no real encryption key is configured.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet

import app.repositories.mediahub_model_repository as mod
from app.core import secret_box
from app.core.secure_settings import MARKER, encrypt_marked
from app.models import MediahubModels
from app.repositories.mediahub_model_repository import MediahubModelRepository


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, scalar_rows: list[Any], mapping_rows: list[Any]) -> None:
        self._scalar_rows = scalar_rows
        self._mapping_rows = mapping_rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._scalar_rows)

    def mappings(self) -> _FakeScalars:
        return _FakeScalars(self._mapping_rows)


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scalar_rows: list[Any] = []
        self.mapping_rows: list[Any] = []

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        self.calls.append((str(stmt), stmt.compile().params))
        return _FakeResult(self.scalar_rows, self.mapping_rows)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def real_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    return key


@pytest.fixture
def repo() -> MediahubModelRepository:
    return MediahubModelRepository()


@pytest.mark.asyncio
async def test_create_binds_ciphertext_not_plaintext(
    real_key, repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [MediahubModels(id=1, name="m1")]
    await repo.create({"name": "m1", "api_key": "sk-raw-create"})

    _sql, binds = fake_session.calls[-1]
    str_binds = [v for v in binds.values() if isinstance(v, str)]
    assert "sk-raw-create" not in str_binds
    assert any(v.startswith(MARKER) for v in str_binds)


@pytest.mark.asyncio
async def test_update_binds_ciphertext_not_plaintext(
    real_key, repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [MediahubModels(id=1, name="m1")]
    await repo.update("1", {"api_key": "sk-raw-update", "updated_at": "now()"})

    _sql, binds = fake_session.calls[-1]
    str_binds = [v for v in binds.values() if isinstance(v, str)]
    assert "sk-raw-update" not in str_binds
    assert any(v.startswith(MARKER) for v in str_binds)


@pytest.mark.asyncio
async def test_update_already_marked_not_double_encrypted(
    real_key, repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    marked = encrypt_marked("sk-x")
    fake_session.scalar_rows = [MediahubModels(id=1, name="m1")]
    await repo.update("1", {"api_key": marked})

    _sql, binds = fake_session.calls[-1]
    assert marked in binds.values()  # passed through verbatim


@pytest.mark.asyncio
async def test_get_by_name_reveals_api_key(
    real_key, repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [
        MediahubModels(id=1, name="m1", api_key=encrypt_marked("sk-stored"))
    ]
    row = await repo.get_by_name("m1")
    assert row is not None
    assert row["api_key"] == "sk-stored"  # repo-transparent for consumers


@pytest.mark.asyncio
async def test_list_all_reveals_api_key(
    real_key, repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [
        MediahubModels(id=1, name="m1", api_key=encrypt_marked("sk-a")),
        MediahubModels(id=2, name="m2", api_key="legacy-plain"),
    ]
    rows = await repo.list_all()
    assert rows[0]["api_key"] == "sk-a"
    assert rows[1]["api_key"] == "legacy-plain"  # legacy plaintext passthrough


@pytest.mark.asyncio
async def test_blank_api_key_untouched(
    real_key, repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [MediahubModels(id=1, name="m1")]
    await repo.create({"name": "m1", "api_key": ""})
    _sql, binds = fake_session.calls[-1]
    assert "" in binds.values()  # blank stays blank (no marker)


@pytest.mark.asyncio
async def test_create_raises_without_real_key(
    monkeypatch, repo: MediahubModelRepository, fake_session: _FakeSession
) -> None:
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("MEDIAHUB_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    with pytest.raises(secret_box.SecretBoxNotConfigured):
        await repo.create({"name": "m1", "api_key": "sk-x"})
    assert fake_session.calls == []  # plaintext never reached the DB layer
