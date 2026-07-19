/**
 * Notification deep-link resolver (W3d).
 *
 * The inbox stores a TYPED link — (link_kind, link_id) — never a raw URL. This
 * pure module maps that typed link plus the effective workspace team id to an
 * in-app route path. Keeping it pure (no router/context imports) makes it
 * unit-testable and the single place route shapes are encoded.
 *
 * link_id is always a string (snowflake ids are JS-precision-unsafe as numbers;
 * issue links carry the human identifier e.g. "MH-42", which the issues route
 * consumes directly).
 */

export type NotificationLinkKind = 'issue' | 'resource' | 'publish_batch';

export interface NotificationLinkInput {
  linkKind?: string | null;
  linkId?: string | null;
  /**
   * Effective workspace team id — the notification's own team_id when present,
   * else the current workspace scope. All app routes are team-scoped
   * (`/team/:teamId/...`), so without it there is no navigable target.
   */
  teamId?: string | null;
}

/**
 * Resolve a notification's typed deep-link to a route path, or `null` when it
 * isn't navigable (unknown/absent link kind, missing id, or no team scope).
 */
export function resolveNotificationLink(input: NotificationLinkInput): string | null {
  const { linkKind, linkId, teamId } = input;
  if (!linkKind || !linkId || !teamId) return null;

  const base = `/team/${teamId}`;
  switch (linkKind) {
    case 'issue':
      // Issues route resolves by human identifier (MH-N), which is what the
      // producer stores in link_id.
      return `${base}/issues/${encodeURIComponent(linkId)}`;
    case 'resource':
      return `${base}/resources/file/${encodeURIComponent(linkId)}`;
    case 'publish_batch':
      // No per-batch route today — land on the distribution records list.
      return `${base}/distribution/records`;
    default:
      return null;
  }
}
