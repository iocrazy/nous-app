"""The cached 「选择音乐」 charts (mig 433).

ORM-model style (``read_scope`` / ``write_scope`` + ``MusicCharts`` /
``MusicChartTracks``); no raw SQL, per the 2026-08-04 全量 ORM 化 约定.

The one rule that is not obvious
================================
**A failed harvest never destroys a good one.** A chart that came back
``ok=False`` updates its own status and timestamp and *leaves the stored tracks
alone*, because the alternative — replacing them with nothing — turns one bad
network minute into an empty tab for the user, and the tab would stay empty
until the next scheduled run. Yesterday's chart is a far better answer than no
chart, as long as the UI can see it is yesterday's (which is what ``fetched_at``
and ``ok`` are for).

The corollary is two timestamps rather than one. ``fetched_at`` is when the
tracks currently stored were read and advances on SUCCESS only; ``checked_at``
is the last attempt and advances every time. Anything rendering "updated N
minutes ago" must read ``fetched_at`` — a single column would let a failed run
stamp yesterday's tracks as fresh, and "updated 1 minute ago" would stay true
through a three-day outage.
"""

from __future__ import annotations

import datetime
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import delete, func, select

from app.db.session import read_scope, write_scope
from app.models import MusicCharts, MusicChartTracks


def _track_payload(track: MusicChartTracks) -> dict[str, Any]:
    """One row → the **existing** `MusicTrack` wire shape.

    ⚠️ The keys are `title` / `author` / `duration`, not the column names.
    `distributionService.ts::MusicTrack` has been that vocabulary since the
    search picker shipped, and the whole point of caching charts is that the
    same card renders them — a second vocabulary at the same boundary would
    make the frontend keep two kinds of track and pick between them.

    `music_id` is stringified rather than passed through: the column is TEXT
    and stays TEXT, and saying so here means a later column-type change cannot
    silently start shipping a lossy JSON number.
    """
    return {
        "music_id": str(track.music_id),
        "title": track.music_name,
        "author": track.music_author,
        "duration": int(track.duration_s or 0),
        # `None` survives as `None`. Zero is a real catalogue value, and the
        # measured chart payloads carry plenty of it.
        "user_count": None if track.user_count is None else int(track.user_count),
        "cover_url": track.cover_url,
        "play_url": track.play_url,
        "position": int(track.position),
    }


#: What `replace_charts` does with one harvested chart.
WRITE_REPLACE = "replace"
WRITE_KEEP = "keep"
WRITE_SKIP = "skip"


def plan_chart_write(chart: Mapping[str, Any]) -> str:
    """One harvested chart → what to do with what is already stored. Pure.

    Extracted from the write loop so the rule can be stated and tested without
    a database, because it is the rule most likely to be "simplified" by a
    later reader into `DELETE + INSERT` unconditionally:

    * ``replace`` — the read succeeded; its tracks are the truth now. An empty
      list is still the truth (an account with no favourites), which is why
      this does not look at the track count.
    * ``keep`` — the read FAILED. Update the status and leave the tracks alone.
      Replacing them with nothing would turn one bad minute into an empty tab
      that stays empty until the next scheduled run, and yesterday's chart is a
      far better answer than none as long as the UI can see it is yesterday's.
    * ``skip`` — the chart carries half an identity. Storing it would create a
      row no later harvest can match (the key is kind + id together), so it
      would leak one dead row per run, forever.
    """
    kind = str(chart.get("category_kind") or "").strip()
    category_id = str(chart.get("category_id") or "").strip()
    if not kind or not category_id:
        return WRITE_SKIP
    return WRITE_REPLACE if bool(chart.get("ok")) else WRITE_KEEP


def read_incoming_tracks(chart: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One harvested chart → the track rows to write. Pure.

    ⚠️ Reads ``songs`` — the BROWSER's wire key (``MusicChartPayload.songs``).
    This module *emits* ``tracks`` because the frontend's long-standing
    ``MusicTrack`` vocabulary uses that word. Two real contracts meet here, and
    this function is the translation.

    Extracted so the translation is testable against a **real** captured
    payload rather than only inside a DB write. For one release both sides said
    ``tracks`` and every counter stayed green — ``success: true``,
    ``charts_read: 12``, ``stored: 12`` — with ``tracks: 0`` the single tell,
    and twelve empty tabs in the UI. The unit tests passed because their
    fixtures used the invented key on *both* sides.

    Entries without an id or a name are dropped rather than filled in: both are
    load-bearing (the id is what gets published, the name is what the user
    reads), and a half-track is worse than a missing one because it looks
    pickable.
    """
    out: list[dict[str, Any]] = []
    for track in chart.get("songs") or ():
        if not isinstance(track, Mapping):
            continue
        music_id = str(track.get("music_id") or "").strip()
        name = str(track.get("music_name") or "").strip()
        if not music_id or not name:
            continue
        raw_count = track.get("user_count")
        try:
            duration_s = max(0, int(track.get("duration_s") or 0))
        except (TypeError, ValueError):
            duration_s = 0
        try:
            # `None` survives. Zero is a real catalogue value.
            user_count = None if raw_count is None else max(0, int(raw_count))
        except (TypeError, ValueError):
            user_count = None
        out.append(
            {
                "music_id": music_id,
                "music_name": name,
                "music_author": str(track.get("music_author") or ""),
                "duration_s": duration_s,
                "user_count": user_count,
                "cover_url": str(track.get("cover_url") or ""),
                "play_url": str(track.get("play_url") or ""),
            }
        )
    return out


class MusicChartsRepository:
    """Read and replace one account's cached chart tabs."""

    async def list_charts(
        self, account_id: int, *, with_tracks: bool = True
    ) -> list[dict[str, Any]]:
        """Every cached tab for this account, in panel order.

        Charts that failed their last refresh are INCLUDED, carrying whatever
        tracks survived from before. Dropping them would make a transient
        failure look like the platform having removed a tab.
        """
        async with read_scope() as session:
            charts = (
                (
                    await session.execute(
                        select(MusicCharts)
                        .where(MusicCharts.account_id == int(account_id))
                        .order_by(MusicCharts.position, MusicCharts.id)
                    )
                )
                .scalars()
                .all()
            )
            if not charts:
                return []

            by_chart: dict[int, list[MusicChartTracks]] = {}
            if with_tracks:
                rows = (
                    (
                        await session.execute(
                            select(MusicChartTracks)
                            .where(
                                MusicChartTracks.chart_id.in_(
                                    [chart.id for chart in charts]
                                )
                            )
                            .order_by(
                                MusicChartTracks.chart_id, MusicChartTracks.position
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for row in rows:
                    by_chart.setdefault(int(row.chart_id), []).append(row)

            return [
                {
                    "id": str(chart.id),
                    "category_id": chart.category_id,
                    "category_kind": chart.category_kind,
                    "category_name": chart.category_name,
                    "position": int(chart.position),
                    "ok": bool(chart.ok),
                    "error": chart.error,
                    "cursor": chart.cursor,
                    "has_more": bool(chart.has_more),
                    "fetched_at": (
                        chart.fetched_at.isoformat() if chart.fetched_at else None
                    ),
                    "checked_at": (
                        chart.checked_at.isoformat() if chart.checked_at else None
                    ),
                    "tracks": [
                        _track_payload(track)
                        for track in by_chart.get(int(chart.id), [])
                    ],
                }
                for chart in charts
            ]

    async def newest_fetch(self, account_id: int) -> Optional[datetime.datetime]:
        """When this account's charts were last *successfully* refreshed.

        Successful only, on purpose: a scheduler that reads "last attempted"
        would back off after a failure exactly when it should retry.
        """
        async with read_scope() as session:
            return (
                await session.execute(
                    select(MusicCharts.fetched_at)
                    .where(
                        MusicCharts.account_id == int(account_id),
                        MusicCharts.ok.is_(True),
                    )
                    .order_by(MusicCharts.fetched_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def replace_charts(
        self,
        account_id: int,
        platform: str,
        charts: Sequence[Mapping[str, Any]],
    ) -> dict[str, int]:
        """Store one harvest. Returns `{"stored": n, "kept": n, "tracks": n}`.

        `stored` counts charts whose tracks were replaced (`ok`), `kept` counts
        charts whose previous tracks were preserved because this attempt failed
        — the number a caller needs to tell "the harvest worked" from "the
        harvest ran".
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        stored = kept = written = 0

        async with write_scope() as session:
            existing = {
                (row.category_kind, row.category_id): row
                for row in (
                    (
                        await session.execute(
                            select(MusicCharts).where(
                                MusicCharts.account_id == int(account_id)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            }

            for position, chart in enumerate(charts):
                plan = plan_chart_write(chart)
                if plan == WRITE_SKIP:
                    continue
                kind = str(chart.get("category_kind") or "").strip()
                category_id = str(chart.get("category_id") or "").strip()
                ok = plan == WRITE_REPLACE
                row = existing.get((kind, category_id))
                if row is None:
                    row = MusicCharts(
                        account_id=int(account_id),
                        platform=platform,
                        category_id=category_id,
                        category_kind=kind,
                        category_name=str(chart.get("category_name") or ""),
                        position=position,
                        ok=ok,
                        error=str(chart.get("error") or ""),
                        cursor=str(chart.get("cursor") or ""),
                        has_more=bool(chart.get("has_more")),
                        fetched_at=now,
                        checked_at=now,
                    )
                    session.add(row)
                    await session.flush()
                    existing[(kind, category_id)] = row
                else:
                    row.platform = platform
                    row.category_name = str(chart.get("category_name") or "")
                    row.position = position
                    row.ok = ok
                    row.error = str(chart.get("error") or "")
                    # Always. This is "we tried".
                    row.checked_at = now
                    if ok:
                        # Only on success. This is "the tracks below are from".
                        row.fetched_at = now
                        row.cursor = str(chart.get("cursor") or "")
                        row.has_more = bool(chart.get("has_more"))

                if not ok:
                    # ⚠️ The rule this module exists to state: a failed attempt
                    # updates its own status and leaves the tracks alone.
                    kept += 1
                    continue

                await session.execute(
                    delete(MusicChartTracks).where(MusicChartTracks.chart_id == row.id)
                )
                # The translation lives in `read_incoming_tracks` so it can be
                # tested against a real captured payload — see its docstring for
                # the release this cost.
                for index, track in enumerate(read_incoming_tracks(chart)):
                    session.add(
                        MusicChartTracks(
                            chart_id=row.id,
                            position=index,
                            music_id=track["music_id"],
                            music_name=track["music_name"],
                            music_author=track["music_author"],
                            duration_s=track["duration_s"],
                            user_count=track["user_count"],
                            cover_url=track["cover_url"],
                            play_url=track["play_url"],
                        )
                    )
                    written += 1
                stored += 1

        return {"stored": stored, "kept": kept, "tracks": written}

    async def list_stale_accounts(
        self, *, ttl_hours: int, limit: int, now: datetime.datetime | None = None
    ) -> list[dict[str, Any]]:
        """Accounts whose cached charts are older than the TTL, oldest first.

        ⚠️ Only accounts that **already have chart rows**. An account nobody has
        ever browsed music for is not returned, and that is the whole policy:
        a harvest costs one draft on a real platform account, so it is opt-in
        per account — the first manual "read charts" is what subscribes it. A
        sweeper that swept every bound account would leave a draft a day on
        accounts the user never uses for music.

        `fetched_at` (not `checked_at`) is the clock, so a run that failed does
        not push the account to the back of the queue — the one moment it most
        needs retrying.
        """
        moment = now or datetime.datetime.now(datetime.timezone.utc)
        cutoff = moment - datetime.timedelta(hours=max(1, ttl_hours))
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(
                        MusicCharts.account_id,
                        MusicCharts.platform,
                        func.max(MusicCharts.fetched_at).label("newest"),
                    )
                    .group_by(MusicCharts.account_id, MusicCharts.platform)
                    .having(func.max(MusicCharts.fetched_at) < cutoff)
                    .order_by(func.max(MusicCharts.fetched_at))
                    .limit(max(1, limit))
                )
            ).all()
        return [
            {
                # A string, like every other id crossing this boundary: these
                # are Snowflake-scale and a JSON number would already be a
                # different value by the time anything read it.
                "account_id": str(row.account_id),
                "platform": str(row.platform or "douyin"),
                "fetched_at": row.newest.isoformat() if row.newest else None,
            }
            for row in rows
        ]

    async def find_track(
        self, account_id: int, music_id: str
    ) -> Optional[dict[str, Any]]:
        """One cached track by its platform id, for building a `music_ref`."""
        key = str(music_id or "").strip()
        if not key:
            return None
        async with read_scope() as session:
            row = (
                await session.execute(
                    select(MusicChartTracks)
                    .join(MusicCharts, MusicCharts.id == MusicChartTracks.chart_id)
                    .where(
                        MusicCharts.account_id == int(account_id),
                        MusicChartTracks.music_id == key,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            return None if row is None else _track_payload(row)


__all__ = [
    "MusicChartsRepository",
    "read_incoming_tracks",
    "WRITE_KEEP",
    "WRITE_REPLACE",
    "WRITE_SKIP",
    "plan_chart_write",
]
