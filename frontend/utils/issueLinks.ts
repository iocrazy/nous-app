/**
 * The one frontend builder of an issue's deep link (B7).
 *
 * Mirror of `backend/app/services/issues/issue_links.py::issue_deep_link`,
 * which 3a Task 3b made the single backend builder. The two print the same
 * string for the same row — the backend stamps `deep_link` onto every lineage
 * version and onto the Generated inbox card, and the frontend assembles the
 * same URL wherever it only has a team and a key. A second spelling would
 * drift by a query string and nothing would fail: the link would simply land
 * at the top of the issue instead of on the step you clicked from.
 *
 * **It refuses more often than it builds**, exactly like the backend one.
 * The route only exists inside a team (`router.tsx` registers
 * `todolist/:identifier` under `/team/:teamId` and NOWHERE else — the
 * team-less `/todolist` is a redirect that takes no identifier), so a link
 * built without a team resolves to nothing. `null` is the honest answer, and
 * the caller renders a chip that does not offer the click.
 */

export interface IssueDeepLinkCoords {
  /** 0-based, so it is compared against `null`/`undefined`, never truth-tested. */
  step?: number | null;
  /** Rides along with a step only: alone it names no position, and the page
   *  can only ignore it (same rule as the backend builder). */
  turn?: number | null;
}

export function issueDeepLink(
  teamId: string | number | null | undefined,
  issueKey: string | null | undefined,
  coords: IssueDeepLinkCoords = {},
): string | null {
  // Falsy, not null-ish: the backend's guard is `if not team_id or not
  // issue_key`, which rejects `0` and `""` as well (L1). A team id of 0 is
  // not a team — spelling the refusal differently on the two sides is the
  // drift this mirror exists to prevent.
  if (!teamId || !issueKey) return null;
  let url = `/team/${teamId}/todolist/${issueKey}`;
  const { step, turn } = coords;
  if (step !== null && step !== undefined) {
    url += `?step=${step}`;
    if (turn !== null && turn !== undefined) url += `&turn=${turn}`;
  }
  return url;
}
