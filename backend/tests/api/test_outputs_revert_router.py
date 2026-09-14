"""POST /api/v1/outputs/{kind}/{ref_id}/revert。

与 test_outputs_router.py 同一条纪律：app 装 register_exception_handlers，所以每个
拒绝都是生产的 ErrorResponse 外壳，类型化码在 details.code。裸 FastAPI 的
{"detail": …} 形状在生产从未出现过（CLAUDE.md 2026-09-09）。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.exceptions import register_exception_handlers

mod = importlib.import_module("app.api.outputs_router")

pytestmark = pytest.mark.unit

ME = "11111111-1111-1111-1111-111111111111"


def _client(monkeypatch, result=None, error=None):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(mod.router, prefix="/api/v1")
    from app.core.deps import get_auth

    app.dependency_overrides[get_auth] = lambda: SimpleNamespace(user_id=ME)
    for dep in mod.router.dependencies:
        app.dependency_overrides[dep.dependency] = lambda: None
    monkeypatch.setattr(
        mod,
        "revert_output",
        AsyncMock(side_effect=error) if error else AsyncMock(return_value=result),
    )
    return TestClient(app)


def _version(v, reverted_from=None):
    return {
        "id": str(700000000000000 + v),
        "version": v,
        "parent_version": v - 1,
        "run_id": None,
        "actor_user_id": ME,
        "reverted_from_version": reverted_from,
        "title": "S1 · Shot 3",
    }


def test_a_successful_revert_returns_201_with_the_new_version(monkeypatch):
    from app.services.deliverables.revert import RevertResult

    r = _client(
        monkeypatch, result=RevertResult(version=_version(4, 1), kept_version=None)
    ).post(
        "/api/v1/outputs/script_shot/9/revert",
        json={"to_version": 1, "expected_latest": 3},
    )
    assert r.status_code == 201, r.text
    body = r.json()["version"]
    assert body["version"] == 4 and body["reverted_from_version"] == 1
    assert body["run_id"] is None and body["actor_user_id"] == ME
    assert r.json()["kept_version"] is None


def test_kept_edits_come_back_as_their_own_version(monkeypatch):
    from app.services.deliverables.revert import RevertResult

    body = (
        _client(
            monkeypatch,
            result=RevertResult(version=_version(5, 1), kept_version=_version(4)),
        )
        .post(
            "/api/v1/outputs/script_shot/9/revert",
            json={"to_version": 1, "expected_latest": 3},
        )
        .json()
    )
    assert body["kept_version"]["version"] == 4
    assert body["kept_version"]["reverted_from_version"] is None


@pytest.mark.parametrize(
    "status_code,code",
    [
        (400, "kind_not_revertible"),
        (404, "version_not_found"),
        (409, "version_conflict"),
        (409, "content_unavailable"),
    ],
)
def test_every_refusal_carries_its_code_in_the_production_envelope(
    monkeypatch, status_code, code
):
    r = _client(
        monkeypatch,
        error=HTTPException(status_code, detail={"code": code, "message": "x"}),
    ).post(
        "/api/v1/outputs/generated_media/5/revert",
        json={"to_version": 1, "expected_latest": 1},
    )
    assert r.status_code == status_code
    assert r.json()["details"]["code"] == code


def test_the_body_is_validated_before_anything_is_touched(monkeypatch):
    """to_version 缺失是 422，不是一次半成品的回退。"""
    r = _client(monkeypatch).post(
        "/api/v1/outputs/script_shot/9/revert", json={"expected_latest": 3}
    )
    assert r.status_code == 422
