"""One version predicate for every daemon-gated path.

The text chain has carried these since 0.3.0; the image gate (P3) needs the
same semantics. Two copies that must agree is the bug class this contract
exists to end — so the module is shared, and the test pins the semantics
the text chain already relies on."""

from app.services.codex.daemon_version import (
    UNVERSIONED,
    parse_version,
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
