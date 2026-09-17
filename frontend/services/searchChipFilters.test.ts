/**
 * The chips have to survive the trip to the search endpoint.
 *
 * They did not: the list applied every chip server-side while `/search/text`
 * was never told about them, so the moment a keyword search returned, the
 * whole toolbar stopped applying — with the chips still drawn as active.
 *
 * Measured on production before the fix, with "AI · transcribed" on:
 * "5000" matched 4 rows of which 1 was transcribed, and the UI showed 4.
 */
import { describe, it, expect } from 'vitest';
import { toSearchChipFilters } from './searchChipFilters';

describe('toSearchChipFilters', () => {
  it('sends nothing when no chip is active', () => {
    // A user who never touched the toolbar must produce the same request as
    // before this existed — that is what makes "no behaviour change for them"
    // checkable rather than asserted.
    expect(toSearchChipFilters(undefined)).toBeUndefined();
    expect(toSearchChipFilters({})).toBeUndefined();
  });

  it('carries the reported chip', () => {
    expect(toSearchChipFilters({ ai_transcribed: true })).toEqual({
      ai_transcribed: true,
    });
  });

  it('drops a chip that is off rather than sending false', () => {
    // false would be a filter the user never asked for; the backend reads it
    // as "no opinion", but sending it at all muddies the request.
    expect(toSearchChipFilters({ ai_transcribed: false })).toBeUndefined();
  });

  it('treats a zero rating as "no opinion", like the list path does', () => {
    expect(toSearchChipFilters({ min_rating: 0 })).toBeUndefined();
    expect(toSearchChipFilters({ min_rating: 3 })).toEqual({ min_rating: 3 });
  });

  it('drops empty arrays', () => {
    expect(
      toSearchChipFilters({ platforms: [], tag_ids: [], aspect_ratios: [] }),
    ).toBeUndefined();
  });

  it('maps the Type chip through the list path\'s own translation', () => {
    // Going straight from the chip value to the wire would search for
    // "video" where parsed_media stores its own vocabulary.
    const out = toSearchChipFilters({ media_types: ['video'] });
    expect(out?.media_types).toBeDefined();
    expect(out?.media_types?.length).toBeGreaterThan(0);
  });

  it('keeps the "matches nothing" sentinel for a type with no wire value', () => {
    // `document` has no counterpart in the web library. Dropping the sentinel
    // would turn "match nothing" into "match everything" — this file's own
    // failure mode, one layer down.
    const out = toSearchChipFilters({ media_types: ['document'] });
    expect(out?.media_types).toEqual(['__impossible__']);
  });

  it('carries every remaining chip', () => {
    const out = toSearchChipFilters({
      tag_ids: ['7'],
      min_rating: 4,
      ai_summarized: true,
      ai_analyzed: true,
      ai_has_prompt: true,
      created_after: '2026-01-01',
      created_before: '2026-12-31',
      duration_min: 10,
      duration_max: 600,
      aspect_ratios: ['9:16'],
      platforms: ['douyin'],
      has_comments: true,
      min_likes: 100,
    });
    expect(out).toMatchObject({
      tag_ids: ['7'],
      min_rating: 4,
      ai_summarized: true,
      ai_analyzed: true,
      ai_has_prompt: true,
      created_after: '2026-01-01',
      created_before: '2026-12-31',
      duration_min: 10,
      duration_max: 600,
      aspect_ratios: ['9:16'],
      platforms: ['douyin'],
      has_comments: true,
      min_likes: 100,
    });
  });

  it('keeps a zero duration bound — 0 is a real lower bound', () => {
    // The falsy-guard used for counts would silently drop it.
    expect(toSearchChipFilters({ duration_min: 0 })).toEqual({
      duration_min: 0,
    });
  });

  it('omits social_combine when no threshold is set', () => {
    // Alone it pins the SQL CASE to a branch with nothing in it.
    expect(toSearchChipFilters({ social_combine: 'or' })).toBeUndefined();
    expect(toSearchChipFilters({ social_combine: 'or', min_likes: 5 })).toEqual({
      min_likes: 5,
      social_combine: 'or',
    });
  });
});
