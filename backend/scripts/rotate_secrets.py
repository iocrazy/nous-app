#!/usr/bin/env python3
"""Idempotent rotate/backfill runner for at-rest encrypted secret columns.

Every column this touches stores a secret that must live encrypted under the
Fernet key in ``MEDIAHUB_TOKEN_ENCRYPTION_KEY`` (see ``app.core.secret_box``).
Rows arrive in three shapes during the migration window:

  * ``NULL``            → nothing to do (skipped).
  * legacy plaintext    → ``encrypt()`` it (backfill).
  * ``gAAAAA`` ciphertext → ``decrypt()`` (MultiFernet: new → OLD → dev fallback)
                            then ``encrypt()`` under the PRIMARY key (rotate).

Because ``secret_box.decrypt`` transparently passes NON-``gAAAAA`` values through
unchanged (legacy read compat), the runner CANNOT tell "decrypted OK" from "was
plaintext" by output alone — so it branches on the ``gAAAAA`` prefix ITSELF
before deciding backfill vs. rotate. A ``gAAAAA`` value that fails to decrypt
(wrong/rotated-out key, tampered) is logged BY PRIMARY KEY ONLY, counted, and
skipped; the run continues.

Running is idempotent: a second pass re-encrypts already-encrypted rows (new IV,
same plaintext) and is a no-op for stats-consumers otherwise.

Usage (inside the backend container / from ``backend/``):

    python -m scripts.rotate_secrets --target user_mcp_servers
    python -m scripts.rotate_secrets --target all --dry-run

``--dry-run`` performs the full scan + stats but issues ZERO UPDATEs.

Secret hygiene: no secret VALUE is ever logged. Failures log the table + column +
row primary key only.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Make ``app.*`` importable when run as ``python scripts/rotate_secrets.py`` too
# (the ``-m scripts.rotate_secrets`` invocation already has ``backend/`` on the
# path via the namespace package).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger  # noqa: E402
from sqlalchemy import inspect as sa_inspect  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy import update as sa_update  # noqa: E402

from app.core import secret_box  # noqa: E402
from app.db.session import read_scope, write_scope  # noqa: E402
from app.models import ApiKeys, UserCookies, UserMcpServers  # noqa: E402

# Fernet tokens are URL-safe base64 of a versioned struct that always begins
# with this prefix. secret_box uses the same sentinel for its legacy passthrough.
_FERNET_PREFIX = "gAAAAA"

# Rows are written in independent executemany chunks of this size so a large
# table rotates in bounded transactions rather than one giant lock.
BATCH_SIZE = 200

# CLI target → (ORM model, secret column names). Reflected class names verified
# against app/models (UserMcpServers / ApiKeys / UserCookies).
TARGETS: dict[str, tuple[type, list[str]]] = {
    "user_mcp_servers": (UserMcpServers, ["bearer_token"]),
    "api_keys": (ApiKeys, ["key_value"]),
    "cookies": (UserCookies, ["cookie_text", "cookie_file"]),
}


@dataclass
class RotateStats:
    """Per-column outcome tally. ``failed`` (decrypt errors) is derived: a
    scanned row lands in exactly one of encrypted / reencrypted / skipped_null,
    or — on a decrypt failure — in none of them."""

    scanned: int = 0
    encrypted: int = 0
    reencrypted: int = 0
    skipped_null: int = 0

    @property
    def failed(self) -> int:
        return self.scanned - self.encrypted - self.reencrypted - self.skipped_null


class _DecryptError(Exception):
    """A ``gAAAAA`` value that could not be decrypted under any configured key."""


def _reencrypt_or_encrypt(value: str) -> tuple[str, bool]:
    """Return ``(new_ciphertext, was_already_encrypted)``.

    Branch on the ``gAAAAA`` prefix OURSELVES (not on decrypt's output — its
    passthrough would hide plaintext). Prefix present → decrypt (may raise) then
    re-encrypt under the primary key; absent → encrypt the legacy plaintext.
    """
    if value.startswith(_FERNET_PREFIX):
        try:
            plaintext = secret_box.decrypt(value)
        except ValueError as exc:
            raise _DecryptError() from exc
        # decrypt only returns None for a None input, which we never pass here.
        assert plaintext is not None
        return secret_box.encrypt(plaintext), True  # type: ignore[return-value]
    return secret_box.encrypt(value), False  # type: ignore[return-value]


async def _flush(model: type, pk_key: str, payload: list[dict[str, Any]]) -> None:
    """Persist one batch via a fresh ``write_scope()`` transaction (ORM bulk
    UPDATE by primary key). No-op on an empty batch."""
    if not payload:
        return
    async with write_scope() as session:
        await session.execute(sa_update(model), payload)
    logger.info("rotate {}: committed {} row(s)", model.__tablename__, len(payload))


async def rotate_column(
    session: Any,
    model: type,
    column_name: str,
    *,
    where: Optional[Any] = None,
    dry_run: bool = False,
) -> RotateStats:
    """Scan ``model.column_name`` through ``session`` and encrypt/rotate every
    non-null value, writing back in ``BATCH_SIZE`` chunks via ``write_scope()``.

    ``session`` is a READ session (the scan). Writes open their own
    ``write_scope()`` transactions so each batch commits independently.
    ``dry_run=True`` scans + tallies but writes nothing.
    """
    pk_key = sa_inspect(model).primary_key[0].key
    pk_attr = getattr(model, pk_key)
    col_attr = getattr(model, column_name)

    stmt = select(pk_attr, col_attr)
    if where is not None:
        stmt = stmt.where(where)

    stats = RotateStats()
    pending: list[dict[str, Any]] = []
    result = await session.execute(stmt)
    for pk, value in result.all():
        stats.scanned += 1
        if value is None:
            stats.skipped_null += 1
            continue
        try:
            new_value, was_encrypted = _reencrypt_or_encrypt(value)
        except _DecryptError:
            # Log the ROW PK only — never the value. Count (via derived .failed)
            # and continue; a rotated-out key shouldn't abort the whole run.
            logger.error(
                "rotate {}.{}: decrypt failed for pk={} — skipped",
                model.__tablename__,
                column_name,
                pk,
            )
            continue
        if was_encrypted:
            stats.reencrypted += 1
        else:
            stats.encrypted += 1
        pending.append({pk_key: pk, column_name: new_value})
        if len(pending) >= BATCH_SIZE and not dry_run:
            await _flush(model, pk_key, pending)
            pending = []

    if pending and not dry_run:
        await _flush(model, pk_key, pending)

    return stats


async def run(target: str, *, dry_run: bool) -> dict[str, RotateStats]:
    """Rotate every column of one target (or ``all``). Returns per-``table.col``
    stats keyed as ``"<target>.<column>"``."""
    names = list(TARGETS) if target == "all" else [target]
    results: dict[str, RotateStats] = {}
    for name in names:
        model, columns = TARGETS[name]
        for column in columns:
            async with read_scope() as session:
                stats = await rotate_column(session, model, column, dry_run=dry_run)
            results[f"{name}.{column}"] = stats
            logger.info(
                "rotate {}.{}: scanned={} encrypted={} reencrypted={} "
                "skipped_null={} failed={} dry_run={}",
                name,
                column,
                stats.scanned,
                stats.encrypted,
                stats.reencrypted,
                stats.skipped_null,
                stats.failed,
                dry_run,
            )
    return results


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Idempotent rotate/backfill for at-rest encrypted secrets."
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=[*TARGETS.keys(), "all"],
        help="Which secret table to rotate (or 'all').",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan + report stats but perform zero UPDATEs.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = _parse_args(argv)
    if not secret_box.is_configured():
        logger.warning(
            "MEDIAHUB_TOKEN_ENCRYPTION_KEY not set — running on the dev "
            "fallback key. Do NOT use this to rotate production secrets."
        )
    asyncio.run(run(args.target, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
