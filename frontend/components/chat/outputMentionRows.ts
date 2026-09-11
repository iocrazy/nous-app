/**
 * Objects (what the endpoint returns) → citable versions (what the picker
 * offers). Harness 3a Task 6.
 *
 * `GET /issues/{id}/outputs` groups by OBJECT because "the agent made three
 * things" and "the agent revised one thing twice" are different facts. A
 * citation, though, names one VERSION — that is the whole point of the chip
 * carrying `version`. This is the one place the two units meet.
 *
 * Shape of the answer: every object's LATEST version first, in the endpoint's
 * own order, then everything older folded into one group below. The reader who
 * types `@` almost always means the thing as it stands now; the older versions
 * have to stay reachable (citing v1 next to v3 is how you say "this changed")
 * but must not pad the list three-deep per object before the second object
 * appears.
 *
 * Pure and React-free so the ordering and the filter are testable without a
 * popover.
 */

import type { OutputObject } from '../../services/outputsService';

/** One citable version — the unit the picker offers and the chip carries. */
export interface OutputMentionRow {
  /** `kind:ref_id:version`. Version is part of the identity: one object cited
   *  at two versions is two citations, not one pick made twice. */
  key: string;
  ref_kind: string;
  ref_id: string;
  version: number;
  /** The registry's title for this version, or the object's, or null. */
  title: string | null;
  /** This is the object's newest registered version. */
  latest: boolean;
  /** First row of the Older group — the list draws the header before it.
   *  True on exactly one row in the whole list, never per object. */
  startsOlderGroup: boolean;
}

/** The kind words a query can match, so an untitled row stays findable. */
const KIND_WORDS: Record<string, string> = {
  generated_media: 'image media generated',
  script_shot: 'shot',
  script_scene: 'scene',
  script_chapter: 'chapter',
};

/** Everything about one object a query is allowed to match. */
function haystack(obj: OutputObject): string {
  return [obj.title ?? '', obj.ref_id, obj.kind, KIND_WORDS[obj.kind] ?? '']
    .join(' ')
    .toLowerCase();
}

/**
 * Build the flat row list for `query`.
 *
 * The filter is per OBJECT, not per version: a title match keeps the whole
 * chain, because the reader is looking for the thing and then choosing which
 * version of it they meant.
 */
export function toMentionRows(objects: OutputObject[], query: string): OutputMentionRow[] {
  const needle = query.trim().toLowerCase();
  const matched = needle
    ? objects.filter((o) => haystack(o).includes(needle))
    : objects;

  const latest: OutputMentionRow[] = [];
  const older: OutputMentionRow[] = [];

  for (const obj of matched) {
    // Newest first, as the endpoint orders them — but sorted again rather than
    // trusted: the group split below is what decides which row is "the thing
    // as it stands now", and reading that off an assumed order would put a
    // stale version at the top of the list on any ordering change.
    const versions = [...(obj.versions ?? [])].sort((a, b) => b.version - a.version);
    versions.forEach((v, idx) => {
      const row: OutputMentionRow = {
        key: `${obj.kind}:${obj.ref_id}:${v.version}`,
        ref_kind: obj.kind,
        ref_id: obj.ref_id,
        version: v.version,
        // The version's own title when it has one — a revision may have been
        // renamed, and the citation records what was registered for THAT
        // version. Falls back to the object's title, then to null; `''` is
        // treated as absent so the row renders its kind word instead of a
        // blank line.
        title: v.title || obj.title || null,
        latest: idx === 0,
        startsOlderGroup: false,
      };
      (idx === 0 ? latest : older).push(row);
    });
  }

  if (older.length > 0) older[0] = { ...older[0], startsOlderGroup: true };
  return [...latest, ...older];
}
