"""Pure projections from ``run_deliverables`` rows to the wire shapes.

Both readers share them — the per-issue list and the per-object lineage must
describe a version the same way, or the frontend ends up with two ideas of what
a version is (三期 3a spec §4).

No IO: the repository has already stringified ids, floated ``cost_cents`` and
ISO-formatted ``created_at``. Nothing here re-derives those.

One version, on the wire (3a Task 3b added the last two keys)::

    {
      "id": "700000000000003", "version": 3, "parent_version": 2,
      "run_id": "913402881190401", "issue_id": "348087075560200",
      "seq": 3, "turn": 2, "step": 3,
      "title": "S1 · Shot 3", "model": "qwen-max", "cost_cents": 1.25,
      "created_at": "2026-09-13T00:00:00+00:00",
      "issue_key": "MH-91",
      "deep_link": "/team/424242424242/todolist/MH-91?step=3&turn=2"
    }

``team_id`` is deliberately NOT in that shape. It arrives on the row, feeds the
link builder, and stops here: the reader needs somewhere to go, not the team's
id.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from app.services.issues.issue_links import issue_deep_link

#: The version fields that cross the wire. An explicit projection, not
#: ``dict(row)``: the row carries columns (and a joined ``issue_id`` /
#: ``issue_key`` / ``team_id``) whose membership in the public shape should be a
#: decision, not a leak.
#:
#: ``deep_link`` is NOT in here, and ``issue_key`` is only half here: neither is
#: a column to copy. ``version_of`` assembles them below — the key can arrive
#: from the per-issue reader instead of the row, and the link is BUILT from
#: (team, key, step, turn) by the one shared builder. Adding either to this
#: tuple would silently replace the assembled value with a raw column (3a 小票
#: A2).
_VERSION_KEYS = (
    "id",
    "version",
    "parent_version",
    "run_id",
    # 3b：人手登记的两列。``ledger_ref`` 刻意**不**在这里——它是服务端定位
    # 字段（ops 行 id），上线等于把它交给浏览器。
    "actor_user_id",
    "reverted_from_version",
    "issue_id",
    "issue_key",
    "seq",
    "turn",
    "step",
    "title",
    "model",
    "cost_cents",
    "created_at",
)


def version_of(
    row: Dict[str, Any],
    *,
    issue_id: Optional[str] = None,
    issue_key: Optional[str] = None,
    team_id: Any = None,
) -> Dict[str, Any]:
    """One row → one ``OutputVersion`` dict.

    The three keyword defaults fill in for the per-issue reader, whose rows were
    selected BY an issue and therefore do not carry the join back (spec §3: the
    table has no ``issue_id`` column — ``agent_runs.issue_id`` is the only
    truth). That reader has already loaded its issue for the visibility check,
    so it hands the identity over rather than paying for a per-row re-JOIN.

    ``deep_link`` comes from the one shared builder, out of the issue KEY and a
    team — never out of ``issue_id``, which no route accepts. It is ``None``
    whenever either is missing, which is the normal state of a run that answers
    to no issue at all.

    **人手版（3b 回退，``run_id IS NULL``）另有一条规则**：它仍然报出所在链的
    ``issue_id`` / ``issue_key``（读者要知道这一版属于哪件工作），但
    ``turn`` / ``step`` / ``deep_link`` 一律 ``None``。轨迹坐标是 run 的东西，
    人手版没有；而 ``deep_link`` 的锚点正是 ``(turn, step)`` —— 给它一条指向
    某次运行某一步的 URL，等于声称这一版是那一步产生的，而它不是。
    """
    out = {key: row.get(key) for key in _VERSION_KEYS}
    if out.get("issue_id") is None and issue_id is not None:
        out["issue_id"] = str(issue_id)
    if out.get("issue_key") is None and issue_key is not None:
        out["issue_key"] = issue_key
    if row.get("run_id") is None:
        out["turn"] = None
        out["step"] = None
        out["deep_link"] = None
        return out
    row_team = row.get("team_id")
    out["deep_link"] = issue_deep_link(
        team_id=row_team if row_team is not None else team_id,
        issue_key=out.get("issue_key"),
        step=out.get("step"),
        # (turn, step) is the trajectory's node key — the step alone is
        # ambiguous on any run that took more than one turn (3a Task 8b).
        turn=out.get("turn"),
    )
    return out


def redact_foreign_issue_links(
    versions: List[Dict[str, Any]], *, visible_issue_ids: Set[str]
) -> List[Dict[str, Any]]:
    """Keep ``issue_key`` / ``deep_link`` only on versions whose issue the
    caller may actually see; blank them everywhere else (3a Task 8b, 小票 A1).

    The per-object reader gates on ONE issue — the newest version's — but each
    row builds its own link out of its own ``team_id``, so an older version
    filed under another issue would otherwise hand the caller a clickable,
    team-scoped URL nobody checked they may follow.

    ``visible_issue_ids`` is the answer to exactly that question, decided per
    issue by ``services.issues.issue_visibility.visible_issue_ids`` and passed
    in as STRING ids. This function does no IO and makes no policy: an id that
    is not in the set loses its link, and so does a version that answers to no
    issue at all (it had none to begin with). The set replaces the earlier
    same-issue-as-the-gate rule, which was deliberately wider than the leak and
    threw away a sibling issue's perfectly visible link.

    ``issue_id`` is left alone: Task 3b ruled the bare snowflake is a
    coordinate, not a route, and it was already on the wire before this.

    Returns new dicts; the inputs are not mutated.
    """
    out: List[Dict[str, Any]] = []
    for version in versions:
        own = version.get("issue_id")
        own = str(own) if own is not None else None
        if own is not None and own in visible_issue_ids:
            out.append(version)
        else:
            out.append({**version, "issue_key": None, "deep_link": None})
    return out


def group_by_object(
    rows: List[Dict[str, Any]],
    *,
    issue_id: Optional[str] = None,
    issue_key: Optional[str] = None,
    team_id: Any = None,
) -> List[Dict[str, Any]]:
    """Group rows into one entry per ``(kind, ref_id)``, versions newest first.

    Object order follows the order objects first appear in ``rows`` (the
    repository already sorts by kind, ref_id, version DESC), so the caller's
    ordering decision is not silently replaced by this function's.

    The entry's ``title`` is the LATEST version's: an old title on a revised
    object names something that no longer exists.
    """
    grouped: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        key = (row.get("kind"), str(row.get("ref_id")))
        entry = grouped.setdefault(
            key,
            {"kind": key[0], "ref_id": key[1], "title": None, "versions": []},
        )
        entry["versions"].append(
            version_of(row, issue_id=issue_id, issue_key=issue_key, team_id=team_id)
        )
    items: List[Dict[str, Any]] = []
    for entry in grouped.values():
        entry["versions"].sort(key=lambda v: v.get("version") or 0, reverse=True)
        latest = entry["versions"][0]
        entry["latest_version"] = latest.get("version") or 0
        entry["title"] = latest.get("title")
        items.append(entry)
    return items


__all__ = ["group_by_object", "redact_foreign_issue_links", "version_of"]
