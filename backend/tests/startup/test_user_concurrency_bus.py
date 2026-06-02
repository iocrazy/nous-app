"""The config.parse_concurrency bus handler must apply the saved cap to ALL
three per-user queues (parse + soda + download), so one Settings knob bounds
every batch-download path."""

from __future__ import annotations

import types

from app.startup import lifecycle_bus


def test_handler_fans_out_to_all_three_setters(monkeypatch):
    calls: dict[str, int] = {}

    monkeypatch.setattr(
        "app.workflows.parse.set_parse_concurrency",
        lambda n: calls.__setitem__("parse", n),
    )
    monkeypatch.setattr(
        "app.workflows.soda_download.set_soda_concurrency",
        lambda n: calls.__setitem__("soda", n),
    )
    monkeypatch.setattr(
        "app.workflows.download.set_download_concurrency",
        lambda n: calls.__setitem__("download", n),
    )

    evt = types.SimpleNamespace(payload={"value": 9})
    lifecycle_bus._on_user_concurrency(evt)

    assert calls == {"parse": 9, "soda": 9, "download": 9}


def test_handler_ignores_missing_value(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "app.workflows.parse.set_parse_concurrency", lambda n: calls.append(n)
    )
    monkeypatch.setattr(
        "app.workflows.soda_download.set_soda_concurrency", lambda n: calls.append(n)
    )
    monkeypatch.setattr(
        "app.workflows.download.set_download_concurrency", lambda n: calls.append(n)
    )
    lifecycle_bus._on_user_concurrency(types.SimpleNamespace(payload={}))
    assert calls == []
