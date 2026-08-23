/**
 * The reference-pool rules. Pure functions, so these are the cheapest place to
 * pin behaviour the UI would otherwise only demonstrate by accident.
 *
 * The properties that matter:
 *   - nine is nine, and frames + templates share it (the model sees one list)
 *   - the person slot REPLACES rather than appends — grabbing a better frame of
 *     yourself must not eat a second slot
 *   - a refused add says WHY; a silent no-op on an Add click is the failure
 *     mode this codebase keeps re-learning
 *   - source_urls puts the person first, because the prompt says "the supplied
 *     character image" and that must be unambiguous
 */
import { describe, expect, it } from 'vitest';

import {
  MAX_REFERENCES,
  addReference,
  formatTimestamp,
  isFull,
  personReference,
  removeReference,
  sourceUrls,
  templateIds,
  type CoverReference,
} from './coverReferences';

const gen = (n: number): string => `34158859979982${n}`;
const url = (n: number): string => `/api/v1/generated-media/${gen(n)}/cover`;

const frame = (n: number, at = 3.1): CoverReference => ({
  kind: 'frame',
  genId: gen(n),
  url: url(n),
  timestampSeconds: at,
});
const template = (n: number, name = 'Bold headline'): CoverReference => ({
  kind: 'template',
  genId: gen(n),
  url: url(n),
  label: name,
  templateId: `tpl-${n}`,
});
const person = (n: number): CoverReference => ({
  kind: 'person',
  genId: gen(n),
  url: url(n),
});

describe('formatTimestamp', () => {
  it('renders mm:ss.s', () => {
    expect(formatTimestamp(3.14)).toBe('0:03.1');
    expect(formatTimestamp(66.42)).toBe('1:06.4');
  });

  it('treats nonsense as zero rather than rendering NaN at the user', () => {
    expect(formatTimestamp(Number.NaN)).toBe('0:00.0');
    expect(formatTimestamp(-5)).toBe('0:00.0');
  });
});

describe('addReference', () => {
  it('appends a frame', () => {
    const r = addReference([], frame(1));
    expect(r.ok).toBe(true);
    expect(r.refs).toHaveLength(1);
  });

  it('refuses a duplicate picture and says why', () => {
    const start = [frame(1)];
    const r = addReference(start, { ...template(1), genId: gen(1) });

    expect(r.ok).toBe(false);
    expect(r).toMatchObject({ reason: 'duplicate' });
    // Same array back, so a caller comparing by identity sees "nothing moved".
    expect(r.refs).toBe(start);
  });

  it('refuses to exceed nine and says why', () => {
    const full = Array.from({ length: MAX_REFERENCES }, (_, i) => frame(i));
    expect(isFull(full)).toBe(true);

    const r = addReference(full, frame(99));

    expect(r.ok).toBe(false);
    expect(r).toMatchObject({ reason: 'full' });
    expect(r.refs).toHaveLength(MAX_REFERENCES);
  });

  it('shares the nine between frames and templates', () => {
    // One flat list, not two budgets: the model sees one list, and two budgets
    // would let a user fill both and silently lose the overflow.
    const mixed = [
      ...Array.from({ length: 5 }, (_, i) => frame(i)),
      ...Array.from({ length: 4 }, (_, i) => template(i + 5)),
    ];
    expect(isFull(mixed)).toBe(true);
    expect(addReference(mixed, template(99)).ok).toBe(false);
  });

  it('REPLACES the person rather than appending a second one', () => {
    const start = [person(1), frame(2)];

    const r = addReference(start, person(3));

    expect(r.ok).toBe(true);
    expect(r.refs.filter((x) => x.kind === 'person')).toHaveLength(1);
    expect(personReference(r.refs)?.genId).toBe(gen(3));
    expect(r.refs).toHaveLength(2);
  });

  it('lets a person in even when the pool is otherwise full', () => {
    // Grabbing a better frame of yourself must not be blocked by a full pool —
    // it does not add a slot, it swaps one.
    const full = Array.from({ length: MAX_REFERENCES }, (_, i) => frame(i));
    const withPerson = [person(50), ...full.slice(1)];

    const r = addReference(withPerson, person(51));

    expect(r.ok).toBe(true);
    expect(r.refs).toHaveLength(MAX_REFERENCES);
    expect(personReference(r.refs)?.genId).toBe(gen(51));
  });
});

describe('removeReference', () => {
  it('drops exactly the one named', () => {
    const refs = removeReference([frame(1), template(2)], gen(1));
    expect(refs).toHaveLength(1);
    expect(refs[0].kind).toBe('template');
  });
});

describe('sourceUrls', () => {
  it('puts the person first so "the supplied character image" is unambiguous', () => {
    const refs = [frame(1), template(2), person(3)];

    expect(sourceUrls(refs)[0]).toBe(url(3));
    expect(sourceUrls(refs)).toHaveLength(3);
  });

  it('is exactly the URL shape the generation bridge accepts', () => {
    // Literal, not a substring: any other shape is dropped silently by
    // generated_media_local_path(), so an "almost right" URL is invisible.
    expect(sourceUrls([frame(1)])[0]).toBe(
      `/api/v1/generated-media/${gen(1)}/cover`,
    );
  });

  it('never emits more than nine even if the list somehow grew', () => {
    const oversized = Array.from({ length: 12 }, (_, i) => frame(i));
    expect(sourceUrls(oversized)).toHaveLength(MAX_REFERENCES);
  });
});

describe('templateIds', () => {
  it('returns only the templates, for the usage counter', () => {
    expect(templateIds([person(1), frame(2), template(3)])).toEqual(['tpl-3']);
  });

  it('is empty when the pool holds no templates', () => {
    expect(templateIds([person(1), frame(2)])).toEqual([]);
  });
});
