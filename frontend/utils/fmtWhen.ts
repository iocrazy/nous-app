/**
 * An ISO instant rendered in the reader's own clock (harness 2b-2 §5-2).
 *
 * Wake-up times are the one number in the issue workbench a person compares
 * against their own watch, so they are never shown in UTC. A value that is
 * not a parseable instant comes back unchanged rather than as "Invalid Date":
 * showing the raw string at least says what the server sent.
 */
export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return '—';
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? new Date(ms).toLocaleString() : iso;
}
