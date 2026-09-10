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

/**
 * The same instant, narrow enough for a thread row: date + HH:mm, no seconds.
 *
 * `fmtWhen`'s full `toLocaleString()` is the widest form a locale has, and in
 * the trajectory's column it shared a flex row with the note — both were
 * `truncate`, so both collapsed to ellipses and the row said nothing (Task 7a
 * defect 8b). Callers that shorten a time this way should keep the full one
 * reachable (a `title`), never drop it.
 *
 * Same contract as `fmtWhen` at the edges: nothing → em dash, unparseable →
 * the raw string, because "what the server sent" beats "Invalid Date".
 */
export function fmtWhenCompact(iso: string | null | undefined): string {
  if (!iso) return '—';
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return iso;
  const d = new Date(ms);
  // The year is dropped only when it is THIS year — a wake-up that fires next
  // January must not read as one that fires in eleven days.
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleString(undefined, {
    ...(sameYear ? {} : { year: 'numeric' }),
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
