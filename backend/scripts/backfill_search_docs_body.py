# backend/scripts/backfill_search_docs_body.py
"""One-time backfill: fill the empty ``search_docs.body`` left by mig 472.

mig 472 projected every historical run and deliverable into ``search_docs``, but
its output arm never listed ``body`` among the inserted columns — so every output
registered before 3c shipped is findable by title only, and the partial trigram
index over ``body`` is empty for all of them. The live writer
(``services/search/projection.py``) has been correct since day one; this is a
one-off backlog, not a leak.

All the reasoning — why this is a script and not a SQL migration, where each
kind's body comes from, and why the "only fill empties" predicate lives in SQL —
is in ``app/services/search/backfill.py``'s module docstring. This file is only
the entry point.

Idempotent: a row that already has a body is not a candidate, so re-running is
safe and cheap. ``--dry-run`` exercises the real read and rebuild paths and skips
only the write.

Local::

    cd backend && uv run python scripts/backfill_search_docs_body.py --dry-run
    cd backend && uv run python scripts/backfill_search_docs_body.py

Production (stdin must be fed with ``-i`` or the heredoc vanishes silently and
you get exit 1 with no output — CLAUDE.md's docker-exec note)::

    docker exec -i -w /app nous-backend /app/.venv/bin/python - <<'PY'
    import asyncio
    from app.services.search.backfill import backfill_search_docs_bodies
    print(asyncio.run(backfill_search_docs_bodies(dry_run=True)))
    PY
"""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger

from app.services.search.backfill import backfill_search_docs_bodies


async def _run(dry_run: bool) -> None:
    stats = await backfill_search_docs_bodies(dry_run=dry_run)
    # ⚠️ Say what each number counts, and keep them apart. "filled=0" alone is
    # ambiguous between "nothing was empty" and "every row failed to rebuild" —
    # two opposite facts. scanned / unavailable / failed are what tell them apart.
    logger.info(
        "backfill {}: {} empty rows scanned, {} filled, {} had no "
        "reconstructible body (chapters and deleted refs — not failures), "
        "{} were filled by the live writer first, {} errored",
        "dry run (nothing written)" if dry_run else "done",
        stats.scanned,
        stats.filled,
        stats.unavailable,
        stats.raced,
        stats.failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read and rebuild for real, write nothing",
    )
    asyncio.run(_run(parser.parse_args().dry_run))


if __name__ == "__main__":
    main()
