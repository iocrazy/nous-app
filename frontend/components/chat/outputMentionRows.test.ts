/**
 * The Outputs tab's row list, as a pure function.
 *
 * The unit the ENDPOINT returns is the object (`{kind, ref_id, title,
 * latest_version, versions[]}`); the unit the PICKER offers is one citable
 * VERSION. Turning one into the other is where "default to the latest" and
 * "fold the rest under Older" actually happen, so it is tested here rather
 * than through a rendered popover.
 *
 * Wire shape copied from `GET /api/v1/issues/{id}/outputs`: every id is a
 * STRING (Snowflake BIGINT), `title` may be null, `versions` arrives newest
 * first as the endpoint orders it, and every version carries `issue_key` plus
 * a finished `deep_link` — both null on a run that answers to no issue, which
 * is why IMAGE below (a canvas-lane run) has neither.
 */
import { describe, expect, it } from 'vitest';

import { toMentionRows } from './outputMentionRows';
import type { OutputObject } from '../../services/outputsService';

const version = (v: number, over: Record<string, unknown> = {}) => ({
  id: `7271452993825342${10 + v}`,
  version: v,
  parent_version: v > 1 ? v - 1 : null,
  run_id: '727145299382534100',
  issue_id: '727145299382534000',
  issue_key: 'MH-91',
  deep_link: `/team/424242424242/todolist/MH-91?step=${v}`,
  seq: null,
  turn: null,
  step: v,
  title: `Shot #1 v${v}`,
  model: 'qwen-max',
  cost_cents: null,
  created_at: '2026-09-10T00:00:00Z',
  ...over,
});

const SHOT: OutputObject = {
  kind: 'script_shot',
  ref_id: '727145299382534999',
  title: 'S3 · Shot #1',
  latest_version: 3,
  versions: [version(3), version(2), version(1)],
};

const IMAGE: OutputObject = {
  kind: 'generated_media',
  ref_id: '727145299382534888',
  title: 'Ava on the pier',
  latest_version: 1,
  // A canvas-lane run: no issue, so no key and no link.
  versions: [version(1, { title: 'Ava on the pier', issue_id: null, issue_key: null, deep_link: null })],
};

describe('toMentionRows', () => {
  it('offers each object at its latest version first', () => {
    const rows = toMentionRows([SHOT, IMAGE], '');
    expect(rows.slice(0, 2)).toMatchObject([
      { ref_id: '727145299382534999', version: 3, latest: true },
      { ref_id: '727145299382534888', version: 1, latest: true },
    ]);
  });

  it('folds every older version below, newest first', () => {
    const rows = toMentionRows([SHOT], '');
    expect(rows.map((r) => r.version)).toEqual([3, 2, 1]);
    expect(rows.map((r) => r.latest)).toEqual([true, false, false]);
  });

  it('marks where the Older group begins, once', () => {
    const rows = toMentionRows([SHOT, IMAGE], '');
    expect(rows.filter((r) => r.startsOlderGroup)).toHaveLength(1);
    // It is the first NON-latest row, not the first row of each object: the
    // header is drawn once for the whole group.
    const marked = rows.find((r) => r.startsOlderGroup)!;
    expect(marked).toMatchObject({ version: 2, latest: false });
  });

  it('draws no Older header when nothing has been revised', () => {
    expect(toMentionRows([IMAGE], '').some((r) => r.startsOlderGroup)).toBe(false);
  });

  it('keys a row by kind, id AND version — the same object twice is two citations', () => {
    // A reader legitimately cites v1 and v3 of one shot in one comment ("this
    // changed"). Keying by object alone would make the second pick replace
    // the first.
    const rows = toMentionRows([SHOT], '');
    expect(new Set(rows.map((r) => r.key)).size).toBe(3);
    expect(rows[0].key).toBe('script_shot:727145299382534999:3');
  });

  it('filters by title, case-insensitively, keeping every version of a match', () => {
    const rows = toMentionRows([SHOT, IMAGE], 'ava');
    expect(rows).toHaveLength(1);
    expect(rows[0].ref_id).toBe('727145299382534888');
  });

  it('filters by ref id, so a snowflake pasted after @ finds its object', () => {
    const rows = toMentionRows([SHOT, IMAGE], '727145299382534999');
    expect(rows.map((r) => r.ref_id)).toEqual([
      '727145299382534999', '727145299382534999', '727145299382534999',
    ]);
  });

  it('matches an untitled object by its kind word rather than dropping it', () => {
    // `title` is nullable in the registry. A filter that only read the title
    // would make every untitled output unfindable the moment the user typed.
    const untitled: OutputObject = { ...SHOT, title: null, versions: [version(1, { title: null })], latest_version: 1 };
    expect(toMentionRows([untitled], 'shot')).toHaveLength(1);
  });

  it('returns nothing for a query that matches nothing', () => {
    expect(toMentionRows([SHOT, IMAGE], 'zzzz')).toEqual([]);
  });

  it('survives an object whose versions list is empty', () => {
    // The endpoint groups by object; a row registered with no version rows is
    // not expected, but a crash in a picker is worse than an absent line.
    const broken: OutputObject = { ...SHOT, versions: [] };
    expect(toMentionRows([broken], '')).toEqual([]);
  });
});
