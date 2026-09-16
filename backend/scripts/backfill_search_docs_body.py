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

Production — run THIS FILE, not a heredoc that re-implements it. ``backend/`` is
copied to ``/app`` (``COPY backend/ .``) and ``.dockerignore`` does not exclude
``scripts/``, so the script is at ``/app/scripts/`` in the image (verified on the
live ``nous-backend``)::

    docker exec -w /app nous-backend /app/.venv/bin/python \
        scripts/backfill_search_docs_body.py --dry-run
    docker exec -w /app nous-backend /app/.venv/bin/python \
        scripts/backfill_search_docs_body.py

A heredoc would be a second copy of the entry point — it can drift from this one
(different args, a stale call signature) and nothing would catch it. Running the
file means the thing tested in CI is the thing that runs in production.

⚠️ No ``-i`` needed here precisely BECAUSE there is no stdin: the ``-i`` rule in
CLAUDE.md applies to ``docker exec ... python -`` fed by a heredoc, where the
heredoc vanishes silently without it and you get exit 1 with no output.

``--limit N`` caps BOTH candidate queries (useful to try a handful first). The
run is idempotent either way — a row that got a body is no longer a candidate.
"""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger

from app.services.search.backfill import backfill_search_docs_bodies


async def _run(dry_run: bool, limit: int | None) -> None:
    stats = await backfill_search_docs_bodies(dry_run=dry_run, limit=limit)
    # ⚠️ Say what each number counts, and keep them apart. "filled=0" alone is
    # ambiguous between "nothing was empty" and "every row failed to rebuild" —
    # two opposite facts. scanned / unavailable / failed are what tell them apart.
    logger.info(
        "backfill {}: {} empty rows scanned, {} filled, {} had no "
        "reconstructible body (chapters and deleted refs — not failures), "
        "{} were filled by the live writer first, {} errored; "
        "{} still-empty projection rows have no run_deliverables row at all, so "
        "this backfill cannot reach them (NOT touched, and NOT part of the "
        "scanned count — investigate separately)",
        "dry run (nothing written)" if dry_run else "done",
        stats.scanned,
        stats.filled,
        stats.unavailable,
        stats.raced,
        stats.failed,
        # 三态：None 是「数不出来」，与「没有孤儿」是相反的结论。
        "unknown" if stats.orphans is None else stats.orphans,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read and rebuild for real, write nothing",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="cap each candidate query (default: no cap)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.dry_run, args.limit))


if __name__ == "__main__":
    main()
