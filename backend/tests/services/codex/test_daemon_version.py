"""One version predicate for every daemon-gated path.

The text chain has carried these since 0.3.0; the image gate (P3) needs the
same semantics. Two copies that must agree is the bug class this contract
exists to end — so the module is shared, and the test pins the semantics
the text chain already relies on."""

from app.services.codex.daemon_version import (
    UNVERSIONED,
    parse_version,
    reported_daemon_version,
    version_at_least,
)


def test_parse_version_reads_plain_and_v_prefixed():
    assert parse_version("0.4.0") == (0, 4, 0)
    assert parse_version("v1.2.3") == (1, 2, 3)


def test_parse_version_ignores_prerelease_tail():
    assert parse_version("0.4.0-rc1") == (0, 4, 0)


def test_unparseable_reads_as_older_than_everything():
    # A garbled report is refused, not waved through.
    assert parse_version("garbage") == (0, 0, 0)
    assert parse_version(None) == (0, 0, 0)
    assert parse_version(UNVERSIONED) == (0, 0, 0)


def test_version_at_least_is_inclusive():
    assert version_at_least("0.4.0", "0.4.0") is True
    assert version_at_least("0.4.1", "0.4.0") is True
    assert version_at_least("0.3.9", "0.4.0") is False
    assert version_at_least(UNVERSIONED, "0.4.0") is False


# ── the default resolver itself ───────────────────────────────────────────
#
# Every gate injects a resolver in its own tests, so the production default —
# the only one that ever runs against a real user — would otherwise be
# exercised nowhere. These three cases ARE the semantics both gates depend on.


async def _resolver_sees(monkeypatch, *, device_id, devices):
    from app.repositories import codex_daemon_repository as repo_mod
    from app.services.codex import daemon_presence

    async def fake_online_device_id(_user_id: str):
        return device_id

    async def fake_list_for_user(_self, _user_id: str):
        return devices

    monkeypatch.setattr(daemon_presence, "online_device_id", fake_online_device_id)
    monkeypatch.setattr(
        repo_mod.CodexDaemonRepository, "list_for_user", fake_list_for_user
    )
    return await reported_daemon_version("u1")


async def test_no_daemon_connected_reports_none_not_a_verdict(monkeypatch):
    # None must stay distinguishable from "old": dispatch skips the gate on it
    # so the truthful daemon_offline is what the user sees.
    assert await _resolver_sees(monkeypatch, device_id=None, devices=[]) is None


async def test_online_device_without_a_reported_version_reads_as_unversioned(
    monkeypatch,
):
    # A connected pre-0.3.0 build — env_report existed, daemon_version did not.
    got = await _resolver_sees(
        monkeypatch,
        device_id="d1",
        devices=[{"id": "d1", "env_report": {"codex_version": "0.9"}}],
    )
    assert got == UNVERSIONED


async def test_online_device_reports_its_version(monkeypatch):
    got = await _resolver_sees(
        monkeypatch,
        device_id="d1",
        devices=[{"id": "d1", "env_report": {"daemon_version": "0.4.0"}}],
    )
    assert got == "0.4.0"
