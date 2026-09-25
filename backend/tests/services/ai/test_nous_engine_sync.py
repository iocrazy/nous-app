"""nous-engine model sync: what the engine's ``/v1/models`` lists becomes catalog rows.

The BYOK provider cards (DeepSeek / Doubao …) ask the provider's ``/v1/models``
and show what they found. The local nous-engine lives in the admin platform
catalog instead (``nous_models``, ``actual_provider='nous'``) and until now every
row was typed in by hand. ``sync_engine_models`` closes that gap:

* a listed service with no row → a row named ``nous-<id>``, credentials copied
  from an existing nous row on the same base_url, plus a zero price row;
* a listed service with a row → ``context_window`` when the engine sends a
  valid one, and ``ready`` → ``last_test_status`` ok/idle when it changed;
* the list is read with ``?include_unready=1`` so it holds every AUTHORIZED
  service, loaded or not: an enabled platform row on this base_url whose
  service is missing had its grant revoked and is disabled; a 401 means the
  whole key was revoked and every enabled platform row on the base_url is
  disabled. Other failures (5xx, timeout) write nothing.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

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
        self.disable_calls: list[list[int]] = []
        self.ready_writes: list[tuple[int, bool]] = []
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

    async def disable_rows(self, ids: list[int]) -> list[str]:
        self.disable_calls.append(list(ids))
        names = []
        for r in self.rows:
            if r["id"] in ids and r["is_enabled"]:
                r["is_enabled"] = False
                names.append(r["name"])
        return names

    async def record_ready(self, row_id: int, ready: bool) -> bool:
        self.ready_writes.append((row_id, ready))
        for r in self.rows:
            if r["id"] == row_id:
                r["last_test_status"] = "ok" if ready else "idle"
        return True


def _mock_engine(payload: Any, status: int = 200) -> respx.Route:
    return respx.get(url__startswith=f"{_BASE}/models").mock(
        return_value=httpx.Response(status, json=payload)
    )


async def _sync(repo: FakeRepo) -> SyncReport:
    return await sync_engine_models(base_url=_BASE, api_key=_KEY, repo=repo)


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
    assert report.unauthorized is False

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
    assert ("<missing id>", "invalid_entry") in [
        (s.id, s.reason) for s in report.skipped
    ]


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
# nous-engine /v1/models contract: include_unready, revocation, 401, ready
# ---------------------------------------------------------------------------

_OTHER_BASE = "http://other-engine:8000/v1"


@pytest.fixture
def caplog_loguru():
    """Messages loguru emitted during the test, as ``"LEVEL message"`` lines."""
    from loguru import logger

    lines: list[str] = []
    sink_id = logger.add(
        lambda m: lines.append(f"{m.record['level'].name} {m.record['message']}"),
        level="DEBUG",
    )
    try:
        yield lines
    finally:
        logger.remove(sink_id)


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
async def test_revoked_service_row_is_disabled_and_reported(caplog_loguru) -> None:
    _mock_engine({"data": [{"id": "qwen3-8-27b", "type": "llm", "ready": False}]})
    repo = FakeRepo([_row(), _asr()])

    report = await _sync(repo)

    assert report.error is None
    assert report.disabled == ("nous-moss-asr",)
    by_name = {r["name"]: r for r in repo.rows}
    assert by_name["nous-moss-asr"]["is_enabled"] is False
    assert by_name["nous-qwen3-8-27b"]["is_enabled"] is True
    assert repo.disable_calls == [[1900000000000000002]]
    assert any(
        "nous-moss-asr" in m and "WARNING" in m for m in caplog_loguru
    ), caplog_loguru


@pytest.mark.asyncio
@respx.mock
async def test_unready_listed_service_is_not_disabled() -> None:
    """``ready=false`` = not loaded right now, never a revocation."""
    _mock_engine(
        {
            "data": [
                {"id": "qwen3-8-27b", "type": "llm", "ready": False},
                {"id": "moss-asr", "type": "asr", "ready": False},
            ]
        }
    )
    repo = FakeRepo([_row(), _asr()])

    report = await _sync(repo)

    assert report.disabled == ()
    assert repo.disable_calls == []
    assert all(r["is_enabled"] for r in repo.rows)


@pytest.mark.asyncio
@respx.mock
async def test_revocation_leaves_other_rows_alone() -> None:
    """Admin-disabled, other-base_url and BYOK rows are never in the batch."""
    _mock_engine({"data": [{"id": "qwen3-8-27b", "type": "llm"}]})
    rows = [
        _row(),
        _asr(is_enabled=False),
        _row(
            id=1900000000000000003,
            name="nous-far",
            actual_model="far-model",
            base_url=_OTHER_BASE,
        ),
        _row(
            id=1900000000000000004,
            name="byok-mine",
            actual_model="mine",
            owner_user_id="0b6f2a54-0000-0000-0000-000000000001",
        ),
    ]
    repo = FakeRepo(rows)

    report = await _sync(repo)

    assert report.disabled == ()
    assert repo.disable_calls == []
    assert [r["is_enabled"] for r in repo.rows] == [True, False, True, True]


@pytest.mark.asyncio
@respx.mock
async def test_relisted_disabled_row_is_not_re_enabled() -> None:
    """An admin's manual disable must not be overwritten by the sync."""
    _mock_engine({"data": [{"id": "moss-asr", "type": "asr", "ready": True}]})
    repo = FakeRepo([_row(), _asr(is_enabled=False)])

    report = await _sync(repo)

    assert {r["name"]: r["is_enabled"] for r in repo.rows}["nous-moss-asr"] is False
    assert repo.ready_writes == []  # disabled rows take no status write either
    assert report.disabled == ("nous-qwen3-8-27b",)


@pytest.mark.asyncio
@respx.mock
async def test_401_disables_every_enabled_platform_row_on_the_base_url(
    caplog_loguru,
) -> None:
    _mock_engine({"detail": "bad key"}, status=401)
    rows = [
        _row(),
        _asr(),
        _row(
            id=1900000000000000003,
            name="nous-far",
            actual_model="far-model",
            base_url=_OTHER_BASE,
        ),
        _row(
            id=1900000000000000004,
            name="byok-mine",
            actual_model="mine",
            owner_user_id="0b6f2a54-0000-0000-0000-000000000001",
        ),
    ]
    repo = FakeRepo(rows)

    report = await _sync(repo)

    assert report.error == "HTTP 401: platform key rejected by nous-engine"
    assert report.unauthorized is True
    assert set(report.disabled) == {"nous-qwen3-8-27b", "nous-moss-asr"}
    assert [r["is_enabled"] for r in repo.rows] == [False, False, True, True]
    assert sum("WARNING" in m for m in caplog_loguru) >= 2


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status", [500, 502, 503, 403])
async def test_non_200_non_401_writes_nothing(status: int) -> None:
    _mock_engine({"detail": "down"}, status=status)
    repo = FakeRepo([_row(), _asr()])

    report = await _sync(repo)

    assert report.error is not None and str(status) in report.error
    assert report.unauthorized is False
    assert report.disabled == ()
    assert repo.disable_calls == [] and repo.ready_writes == []


@pytest.mark.asyncio
@respx.mock
async def test_timeout_writes_nothing() -> None:
    respx.get(url__startswith=f"{_BASE}/models").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    repo = FakeRepo([_row(), _asr()])

    report = await _sync(repo)

    assert report.error is not None and "ReadTimeout" in report.error
    assert report.disabled == () and repo.disable_calls == []


@pytest.mark.asyncio
@respx.mock
async def test_ready_writes_ok_and_idle_only_on_change() -> None:
    _mock_engine(
        {
            "data": [
                {"id": "qwen3-8-27b", "type": "llm", "ready": True},
                {"id": "moss-asr", "type": "asr", "ready": False},
            ]
        }
    )
    repo = FakeRepo([_row(last_test_status="idle"), _asr(last_test_status="idle")])

    first = await _sync(repo)
    second = await _sync(repo)

    assert repo.ready_writes == [(1900000000000000001, True)]  # asr already idle
    assert first.ready_changed == 1
    assert second.ready_changed == 0


@pytest.mark.asyncio
@respx.mock
async def test_ready_false_turns_ok_row_idle() -> None:
    _mock_engine({"data": [{"id": "qwen3-8-27b", "type": "llm", "ready": False}]})
    repo = FakeRepo([_row(last_test_status="ok")])

    report = await _sync(repo)

    assert repo.ready_writes == [(1900000000000000001, False)]
    assert report.ready_changed == 1


@pytest.mark.asyncio
@respx.mock
async def test_ready_is_ignored_for_image_rows_and_non_bool_values() -> None:
    _mock_engine(
        {
            "data": [
                {"id": "studio-upscale", "type": "image", "ready": True},
                {"id": "qwen3-8-27b", "type": "llm", "ready": "yes"},
                {"id": "moss-asr", "type": "asr"},
            ]
        }
    )
    image = _row(
        id=1900000000000000005,
        name="nous-studio-upscale",
        type="image",
        actual_model="studio-upscale",
        last_test_status="not_probed",
    )
    repo = FakeRepo([_row(), _asr(), image])

    report = await _sync(repo)

    assert repo.ready_writes == []
    assert report.ready_changed == 0


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


def test_merge_reports_carries_disabled_ready_and_unauthorized() -> None:
    merged = nous_engine_sync.merge_reports(
        [
            SyncReport(disabled=("a",), ready_changed=2),
            SyncReport(
                disabled=("b", "c"),
                ready_changed=1,
                unauthorized=True,
                error="HTTP 401: platform key rejected by nous-engine",
            ),
        ]
    )
    assert merged.disabled == ("a", "b", "c")
    assert merged.ready_changed == 3
    assert merged.unauthorized is True


@pytest.mark.asyncio
@respx.mock
async def test_empty_list_is_not_trusted_as_mass_revocation(caplog_loguru) -> None:
    """200 + ``data=[]`` could be an engine fault as easily as "all revoked";
    disabling everything would cost the admin a row-by-row manual restore.
    A real full revocation deletes the key and takes the 401 path."""
    _mock_engine({"data": []})
    repo = FakeRepo([_row(), _asr()])

    report = await _sync(repo)

    assert report.error is None
    assert report.disabled == () and repo.disable_calls == []
    assert all(r["is_enabled"] for r in repo.rows)
    assert [(s.id, s.reason) for s in report.skipped] == [
        ("<all>", "empty_list_not_trusted")
    ]
    assert any(
        m.startswith("WARNING") and "empty" in m for m in caplog_loguru
    ), caplog_loguru
