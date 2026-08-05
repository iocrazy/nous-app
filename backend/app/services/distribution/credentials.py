"""Douyin 平台凭证 — 只从 DB system_settings 读（key: distribution.douyin），
无 env fallback（配置 env→DB 铁律）。

settings 值形如: {"client_key": "...", "client_secret": "...", "redirect_uri": "https://.../callback"}
"""

from __future__ import annotations

import json

from app.services.distribution.douyin_adapter import DouyinCredentials

SETTINGS_KEY = "distribution.douyin"


class CredentialsNotConfigured(RuntimeError):
    pass


def _parse_douyin_settings(value) -> DouyinCredentials:
    if not isinstance(value, dict):
        raise CredentialsNotConfigured(f"system_settings['{SETTINGS_KEY}'] missing")
    try:
        return DouyinCredentials(
            client_key=value["client_key"],
            client_secret=value["client_secret"],
            redirect_uri=value["redirect_uri"],
        )
    except KeyError as exc:
        raise CredentialsNotConfigured(f"{SETTINGS_KEY} missing field {exc}") from exc


async def get_douyin_credentials() -> DouyinCredentials:
    """Read ``distribution.douyin`` from ``system_settings`` (DB-only, no env
    fallback) and decrypt any ``enc:v1:``-marked fields (forward-compatible
    with encrypting ``client_secret`` at rest later; today it's plaintext —
    see the module-level note in the PR).

    The ORM read normally hands the jsonb ``value`` column back as an
    already-decoded dict, but stay tolerant of the JSON-string shape too
    (same defensive fallback as ``app.workflows.thumbnail._backfill_scan_step``)
    in case a driver/codec path ever returns the raw text. ``json.loads`` runs
    BEFORE ``reveal`` so that any ``enc:v1:``-marked sub-fields are recognized
    as nested dict values, not buried inside an un-decoded JSON string (which
    ``reveal`` would pass through unchanged).
    """
    from sqlalchemy import select

    from app.core.secure_settings import reveal
    from app.db.session import read_scope
    from app.models import SystemSettings

    async with read_scope() as session:
        raw = await session.scalar(
            select(SystemSettings.value).where(SystemSettings.key == SETTINGS_KEY)
        )
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    value = reveal(raw)
    return _parse_douyin_settings(value)
