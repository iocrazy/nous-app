"""nous-engine model sync: what the engine lists and the catalog lacks becomes a row.

Since spec 2026-09-25 §3.4 this runs only from the admin "Sync from
nous-engine" button, and only creates rows / refreshes windows:

* a listed service with no row → a row named ``nous-<id>``, credentials copied
  from an existing nous row on the same base_url, plus a zero price row;
* a listed service with a row → ``context_window`` when the engine sends a
  valid one;
* the list is ``engine_catalog.engine_snapshot`` (``?include_unready=1``);
  one that cannot be read — 401, 5xx, transport, stale — is an ``error`` and
  writes nothing. Nothing is ever disabled or re-statused: authorization and
  ``ready`` are read live by the platform view.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

import app.services.ai.engine_catalog as ec
from app.services.ai import nous_engine_sync
from app.services.ai.nous_engine_sync import (
    SyncReport,
    display_name_for,
    sync_engine_models,
)

_BASE = "http://host.docker.internal:8000/v1"
_KEY = "sk-nous-plain"
_CIPHER = "enc:v1:ciphertext-of-the-platform-key"

_ENGINE_PAYLOAD = {
    "object": "list",
    "data": [
        {"id": "qwen3-8-27b-orcarouter", "type": "inference"},
        {"id": "wemm-embedding-2b", "type": "embedding"},
        {"id": "krea2", "type": "app"},
        {"id": "qwen3-8-27b", "type": "llm", "context_window": 32768},
    ],
}


def _row(**over: Any) -> dict[str, Any]:
    base = {
        # Snowflake ids are JSON numbers / Python ints on this path.
        "id": 1900000000000000001,
        "name": "nous-qwen3-8-27b",
        "display_name": "Nous Qwen3 8 27B",
        "type": "llm",
        "actual_provider": "nous",
        "actual_model": "qwen3-8-27b",
        "api_key": _CIPHER,
        "app_id": None,
        "base_url": _BASE,
        "pricing_type": "per_hour",
        "pricing_value": 0,
        "is_enabled": True,
        "sort_order": 0,
        "owner_user_id": None,
        "context_window_tokens": None,
        "last_test_status": None,
    }
    base.update(over)
    return base


class FakeRepo:
    """In-memory stand-in for NousEngineSyncRepository (same method surface)."""

    def __init__(
        self, rows: list[dict[str, Any]], other_names: tuple[str, ...] = ()
    ) -> None:
        self.rows = [dict(r) for r in rows]
        self.other_names = set(other_names)
        self.prices: list[dict[str, Any]] = []
        self.window_writes: list[tuple[int, int]] = []
        self._next_id = 1900000000000000100

    async def list_engine_rows(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows if r["actual_provider"] == "nous"]

    async def names_taken(self, names: list[str]) -> set[str]:
        own = {r["name"] for r in self.rows} | self.other_names
        return {n for n in names if n in own}

    async def max_sort_order(self) -> int:
        return max([r["sort_order"] for r in self.rows] + [53])

    async def insert_engine_row(
        self, values: dict[str, Any], *, supports_vision: bool
    ) -> dict[str, Any]:
        row = {**values, "id": self._next_id, "context_window_tokens": None}
        row.update({k: v for k, v in values.items()})
        self._next_id += 1
        self.rows.append(row)
        if not any(p["model"] == values["name"] for p in self.prices):
            self.prices.append(
                {
                    "model": values["name"],
                    "provider": "nous",
                    "prompt": 0,
                    "completion": 0,
                    "supports_vision": supports_vision,
                }
            )
        return {"id": row["id"], "name": row["name"]}

    async def set_context_window(self, row_id: int, tokens: int) -> bool:
        self.window_writes.append((row_id, tokens))
        for r in self.rows:
            if r["id"] == row_id:
                r["context_window_tokens"] = tokens
        return True


def _mock_engine(payload: Any, status: int = 200) -> respx.Route:
    return respx.get(url__startswith=f"{_BASE}/models").mock(
        return_value=httpx.Response(status, json=payload)
    )


async def _sync(repo: FakeRepo) -> SyncReport:
    return await sync_engine_models(base_url=_BASE, api_key=_KEY, repo=repo)


@pytest.fixture(autouse=True)
def _fresh_engine_cache():
    """The list is the cached engine snapshot; each test scripts its own."""
    ec.reset_engine_cache()
    yield
    ec.reset_engine_cache()


@pytest.mark.asyncio
@respx.mock
async def test_creates_updates_and_skips_by_type() -> None:
    route = _mock_engine(_ENGINE_PAYLOAD)
    repo = FakeRepo([_row()])

    report = await _sync(repo)

    assert report.error is None
    assert report.discovered == 4
    assert set(report.created) == {
        "nous-qwen3-8-27b-orcarouter",
        "nous-wemm-embedding-2b",
    }
    assert report.updated == ("nous-qwen3-8-27b",)
    assert [(s.id, s.reason) for s in report.skipped] == [
        ("krea2", "unsupported_type:app")
    ]
    # bearer = the platform key the caller passed (plaintext, never the cipher)
    assert route.calls.last.request.headers["authorization"] == f"Bearer {_KEY}"


@pytest.mark.asyncio
@respx.mock
async def test_new_rows_copy_credentials_and_get_zero_prices() -> None:
    _mock_engine(_ENGINE_PAYLOAD)
    repo = FakeRepo([_row(app_id="app-1", pricing_type="per_hour", sort_order=5)])

    await _sync(repo)

    by_name = {r["name"]: r for r in repo.rows}
    orca = by_name["nous-qwen3-8-27b-orcarouter"]
    emb = by_name["nous-wemm-embedding-2b"]
    for new in (orca, emb):
        assert new["actual_provider"] == "nous"
        assert new["api_key"] == _CIPHER  # copied verbatim, not re-encrypted
        assert new["base_url"] == _BASE
        assert new["app_id"] == "app-1"
        assert new["pricing_type"] == "per_hour"
        assert new["pricing_value"] == 0
        assert new["owner_user_id"] is None
        assert new["is_enabled"] is True
    assert orca["type"] == "llm"  # engine 'inference' → catalog 'llm'
    assert orca["actual_model"] == "qwen3-8-27b-orcarouter"
    assert orca["display_name"] == "Nous Qwen3 8 27B Orcarouter"
    assert emb["type"] == "embedding"
    # appended after the current maximum (53 in the fake), one slot each
    assert sorted([orca["sort_order"], emb["sort_order"]]) == [54, 55]
    assert sorted(p["model"] for p in repo.prices) == [
        "nous-qwen3-8-27b-orcarouter",
        "nous-wemm-embedding-2b",
    ]
    assert all(p["prompt"] == 0 and p["completion"] == 0 for p in repo.prices)


@pytest.mark.asyncio
@respx.mock
async def test_existing_row_gets_window_written_once() -> None:
    _mock_engine(_ENGINE_PAYLOAD)
    repo = FakeRepo([_row()])

    await _sync(repo)

    assert repo.window_writes == [(1900000000000000001, 32768)]


@pytest.mark.asyncio
@respx.mock
async def test_second_run_is_idempotent() -> None:
    _mock_engine(_ENGINE_PAYLOAD)
    repo = FakeRepo([_row()])

    await _sync(repo)
    second = await _sync(repo)

    assert second.created == ()
    assert second.updated == ()  # window already 32768
    assert len(repo.prices) == 2
    assert len([r for r in repo.rows if r["actual_model"] == "wemm-embedding-2b"]) == 1


@pytest.mark.asyncio
@respx.mock
async def test_name_taken_by_another_row_is_skipped_not_renamed() -> None:
    _mock_engine({"data": [{"id": "wemm-embedding-2b", "type": "embedding"}]})
    # e.g. a doubao row someone named nous-wemm-embedding-2b
    repo = FakeRepo([_row()], other_names=("nous-wemm-embedding-2b",))

    report = await _sync(repo)

    assert report.created == ()
    assert [(s.id, s.reason) for s in report.skipped] == [
        ("wemm-embedding-2b", "name_taken")
    ]
    assert repo.prices == []


@pytest.mark.asyncio
@respx.mock
async def test_missing_data_is_a_typed_error() -> None:
    _mock_engine({"object": "list"})
    repo = FakeRepo([_row()])

    report = await _sync(repo)

    assert report.error is not None and "data" in report.error
    assert report.discovered == 0
    assert report.created == () and report.updated == ()


@pytest.mark.asyncio
@respx.mock
async def test_http_error_and_transport_error_are_typed_errors() -> None:
    _mock_engine({"detail": "boom"}, status=500)
    report = await _sync(FakeRepo([_row()]))
    assert report.error is not None and "500" in report.error

    ec.reset_engine_cache()
    respx.get(url__startswith=f"{_BASE}/models").mock(
        side_effect=httpx.ConnectError("refused")
    )
    report = await _sync(FakeRepo([_row()]))
    assert report.error is not None and "ConnectError" in report.error


@pytest.mark.asyncio
@respx.mock
async def test_no_credential_source_on_this_base_url_skips_creation() -> None:
    _mock_engine({"data": [{"id": "moss-asr", "type": "asr"}]})
    repo = FakeRepo([_row(base_url="http://other-engine:8000/v1")])

    report = await _sync(repo)

    assert report.created == ()
    assert [(s.id, s.reason) for s in report.skipped] == [
        ("moss-asr", "no_credential_source")
    ]


@pytest.mark.asyncio
@respx.mock
async def test_bad_window_values_are_ignored() -> None:
    _mock_engine(
        {
            "data": [
                {"id": "qwen3-8-27b", "type": "llm", "context_window": "32k"},
                {"id": "qwen3-8-27b", "type": "llm", "context_window": 0},
                {"id": "qwen3-8-27b", "type": "llm", "context_window": True},
                {"type": "llm"},
            ]
        }
    )
    repo = FakeRepo([_row()])

    report = await _sync(repo)

    assert repo.window_writes == []
    assert report.updated == () and report.created == ()


@pytest.mark.asyncio
@respx.mock
async def test_vision_capability_seeds_new_price_row_when_present() -> None:
    _mock_engine(
        {
            "data": [
                {
                    "id": "qwen3-vl-8b",
                    "type": "llm",
                    "capabilities": {"vision": True, "tools": True},
                    "ready": False,
                }
            ]
        }
    )
    repo = FakeRepo([_row()])

    await _sync(repo)

    assert repo.prices == [
        {
            "model": "nous-qwen3-vl-8b",
            "provider": "nous",
            "prompt": 0,
            "completion": 0,
            "supports_vision": True,
        }
    ]


@pytest.mark.parametrize(
    ("service_id", "expected"),
    [
        ("qwen3-8-27b-huihui", "Nous Qwen3 8 27B Huihui"),
        ("wemm-embedding-2b", "Nous Wemm Embedding 2B"),
        ("moss-asr", "Nous Moss Asr"),
        ("studio_upscale", "Nous Studio Upscale"),
    ],
)
def test_display_name_for(service_id: str, expected: str) -> None:
    assert display_name_for(service_id) == expected


def test_type_map_matches_catalog_vocabulary() -> None:
    from typing import get_args

    from app.schemas.nous_model import NousModelType

    assert set(nous_engine_sync.ENGINE_TYPE_TO_CATALOG.values()) <= set(
        get_args(NousModelType)
    )


# ---------------------------------------------------------------------------
# What the sync no longer does: disable, re-status, trust a stale list
# ---------------------------------------------------------------------------


def _asr(**over: Any) -> dict[str, Any]:
    return _row(
        id=1900000000000000002,
        name="nous-moss-asr",
        type="asr",
        actual_model="moss-asr",
        **over,
    )


@pytest.mark.asyncio
@respx.mock
async def test_list_is_read_with_include_unready_and_nothing_else() -> None:
    route = _mock_engine({"data": []})

    await _sync(FakeRepo([_row()]))

    params = route.calls.last.request.url.params
    assert params.get("include_unready") == "1"
    assert "include_unloaded" not in params


@pytest.mark.asyncio
@respx.mock
async def test_a_service_missing_from_the_list_leaves_its_row_alone() -> None:
    _mock_engine({"data": [{"id": "qwen3-8-27b", "type": "llm", "ready": False}]})
    repo = FakeRepo([_row(), _asr(last_test_status="ok")])
    before = [dict(r) for r in repo.rows]

    report = await _sync(repo)

    assert report.error is None
    assert repo.rows == before  # nothing disabled, no status written
    assert not hasattr(repo, "disable_rows") and not hasattr(repo, "record_ready")


@pytest.mark.asyncio
@respx.mock
async def test_401_is_an_error_and_writes_nothing() -> None:
    _mock_engine({"error": "invalid key"}, status=401)
    repo = FakeRepo([_row(), _asr()])
    before = [dict(r) for r in repo.rows]

    report = await _sync(repo)

    assert report.error == ec.UNAUTHORIZED_ERROR
    assert report.discovered == 0 and report.created == ()
    assert repo.rows == before and repo.window_writes == []


@pytest.mark.asyncio
async def test_a_stale_snapshot_is_an_error_not_a_list_to_create_from(
    monkeypatch,
) -> None:
    """Keep-last-good is fine for showing a status; creating rows an admin
    asked for NOW from a carried-over list is not."""
    answers = [
        ec._Read(
            services={
                "moss-asr": ec.EngineService(
                    id="moss-asr",
                    type="asr",
                    ready=True,
                    context_window=None,
                    capabilities=None,
                )
            }
        ),
        ec._Read(error="ReadTimeout: slow"),
    ]

    async def _fetch(base_url, api_key):
        return answers.pop(0)

    clock = [1000.0]
    monkeypatch.setattr(ec, "_fetch", _fetch)
    monkeypatch.setattr(ec, "_clock", lambda: clock[0])
    await ec.engine_snapshot(_BASE, _KEY)  # the last good read
    clock[0] += ec.ENGINE_TTL_S + 1
    ec._cache.clear()  # TTL passed; the next read fails

    repo = FakeRepo([_row()])
    report = await _sync(repo)

    assert report.error is not None and "ReadTimeout" in report.error
    assert report.created == () and len(repo.rows) == 1


@pytest.mark.asyncio
@respx.mock
async def test_ready_is_never_written() -> None:
    _mock_engine({"data": [{"id": "qwen3-8-27b", "type": "llm", "ready": False}]})
    repo = FakeRepo([_row(last_test_status="ok")])

    await _sync(repo)

    assert repo.rows[0]["last_test_status"] == "ok"


@pytest.mark.asyncio
@respx.mock
async def test_null_window_and_capabilities_never_overwrite() -> None:
    """Non-model services send ``context_window: null`` / ``capabilities: null``."""
    _mock_engine(
        {
            "data": [
                {
                    "id": "qwen3-8-27b",
                    "type": "llm",
                    "context_window": None,
                    "capabilities": None,
                    "ready": True,
                },
                {
                    "id": "wemm-embedding-2b",
                    "type": "embedding",
                    "context_window": None,
                    "capabilities": None,
                },
            ]
        }
    )
    repo = FakeRepo([_row(context_window_tokens=65536, last_test_status="ok")])

    report = await _sync(repo)

    assert repo.window_writes == []
    assert report.updated == ()
    assert {r["name"]: r for r in repo.rows}["nous-qwen3-8-27b"][
        "context_window_tokens"
    ] == 65536
    new = {r["name"]: r for r in repo.rows}["nous-wemm-embedding-2b"]
    assert new["context_window_tokens"] is None
    assert repo.prices[0]["supports_vision"] is False


def test_merge_reports_concatenates_and_joins_errors() -> None:
    merged = nous_engine_sync.merge_reports(
        [
            SyncReport(discovered=2, created=("a",)),
            SyncReport(error="HTTP 401: platform key rejected by nous-engine"),
            SyncReport(discovered=1, updated=("b",), error="HTTP 500: boom"),
        ]
    )
    assert merged.discovered == 3
    assert merged.created == ("a",) and merged.updated == ("b",)
    assert merged.error == (
        "HTTP 401: platform key rejected by nous-engine; HTTP 500: boom"
    )


@pytest.mark.asyncio
@respx.mock
async def test_empty_list_creates_nothing_and_disables_nothing() -> None:
    _mock_engine({"data": []})
    repo = FakeRepo([_row(), _asr()])
    before = [dict(r) for r in repo.rows]

    report = await _sync(repo)

    assert report.error is None
    assert report.discovered == 0 and report.created == ()
    assert repo.rows == before
