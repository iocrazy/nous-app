"""One version predicate for every daemon-gated path.

The text chain (``ai/adapters/codex_daemon.py``) has gated on these since
0.3.0. The image chain needs the same semantics, and two copies that must
agree is exactly the bug class this contract exists to end — so they live
here and both chains import them.

Each chain keeps its OWN minimum (``MIN_TEXT_DAEMON_VERSION`` and the image
gate's equivalent): the minimum is a policy value that moves per feature,
while the comparison is shared machinery.
"""

from __future__ import annotations

import re
from typing import Optional

# What a connected daemon that reports no version at all is treated as. It is
# a real pre-0.3.0 build (``daemon_version`` did not exist before), and going
# through the same integer comparison as everything else keeps one code path.
UNVERSIONED = "0.0.0"

_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)")


def parse_version(raw: object) -> tuple[int, int, int]:
    """``"0.3.0"`` → ``(0, 3, 0)``. Unparseable → ``(0, 0, 0)``.

    Falling back to zeros means a garbled report reads as "older than every
    real release" and is refused, rather than being waved through because we
    could not understand it. Trailing junk is ignored on purpose so a
    prerelease tag (``0.3.0-rc1``) still compares as 0.3.0.
    """
    match = _VERSION_RE.match(str(raw or ""))
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def version_at_least(reported: object, minimum: str) -> bool:
    return parse_version(reported) >= parse_version(minimum)


async def reported_daemon_version(user_id: str) -> Optional[str]:
    """Version of the daemon currently holding this user's socket.

    ``None`` means "no daemon is connected, or we could not find out" — NOT a
    verdict. The caller must skip the gate on ``None`` so that dispatch raises
    the truthful ``daemon_offline`` a moment later; telling someone to update
    a daemon that is not running is the worse of the two wrong answers.

    A device that IS online but whose ``env_report`` carries no
    ``daemon_version`` returns ``UNVERSIONED`` — that is a real pre-0.3.0
    build, and it is exactly the case this gate exists for.
    """
    from app.repositories.codex_daemon_repository import CodexDaemonRepository
    from app.services.codex import daemon_presence

    device_id = await daemon_presence.online_device_id(user_id)
    if not device_id:
        return None
    devices = await CodexDaemonRepository().list_for_user(user_id)
    row = next((d for d in devices if str(d.get("id")) == str(device_id)), None)
    if row is None:
        # Presence and the table disagree (revoked mid-flight, replica lag).
        # Not enough to convict a version on.
        return None
    report = row.get("env_report")
    version = report.get("daemon_version") if isinstance(report, dict) else None
    if isinstance(version, str) and version.strip():
        return version
    return UNVERSIONED


__all__ = [
    "UNVERSIONED",
    "parse_version",
    "reported_daemon_version",
    "version_at_least",
]
