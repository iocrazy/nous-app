// frontend/components/DownloadsView/localSearchMatch.ts
//
// The instant local filter that runs while the debounced backend search is in
// flight. It used to match a fixed set of fields regardless of which scope
// checkboxes the user had ticked, so the list disagreed with what the backend
// was about to return.
//
// Only the fields the paginated ``parsed_media`` rows actually carry can be
// judged here; ``notes`` and ``transcript`` live on other tables. When the
// user's scope contains none of the locally-judgeable fields, this pass MUST
// NOT filter: the backend is the only thing that can answer, and hiding every
// row meanwhile renders the "no downloaded content yet" empty state, which
// tells the user their library is gone. That is worse than showing an
// unfiltered page for a moment -- and it is not always a moment: a
// single-character query never reaches the backend at all (DownloadsView only
// fires quick-search at two characters or more), so filtering here would leave
// the grid permanently empty with no error anywhere.

import type { SearchField } from '../../services/searchService';

export interface LocalSearchRow {
  title?: string | null;
  author_nickname?: string | null;
  author?: string | null;
  description?: string | null;
  hashtags?: string | null;
}

/** How each locally-judgeable scope reads its text off a library row.
 *
 *  This map is the single source of truth: ``LOCALLY_MATCHABLE_FIELDS`` is
 *  derived from its keys and ``matchesLocalSearch`` iterates it, so adding a
 *  scope here is the only edit needed and cannot half-land. */
const LOCAL_FIELD_READERS: Partial<
  Record<SearchField, (row: LocalSearchRow, tagText: string) => string[]>
> = {
  title: (row) => [row.title ?? ''],
  description: (row) => [row.description ?? ''],
  author: (row) => [row.author_nickname ?? '', row.author ?? ''],
  hashtags: (row) => [row.hashtags ?? ''],
  tags: (_row, tagText) => [tagText],
};

/** The subset of scopes the local pass can evaluate from a library row. */
export const LOCALLY_MATCHABLE_FIELDS = Object.keys(
  LOCAL_FIELD_READERS,
) as SearchField[];

/**
 * @param tagText Pre-joined tag names for this row (empty string when unknown).
 */
export const matchesLocalSearch = (
  row: LocalSearchRow,
  query: string,
  scope: readonly string[],
  tagText: string = '',
): boolean => {
  const q = query.toLowerCase().trim();
  if (!q) return true;

  const judgeable = LOCALLY_MATCHABLE_FIELDS.filter((f) => scope.includes(f));
  // Nothing here can decide this scope -- defer to the backend rather than
  // asserting the library is empty.
  if (judgeable.length === 0) return true;

  return judgeable.some((field) =>
    (LOCAL_FIELD_READERS[field]?.(row, tagText) ?? []).some((value) =>
      value.toLowerCase().includes(q),
    ),
  );
};
