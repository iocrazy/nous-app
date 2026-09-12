import { describe, expect, it } from 'vitest';

import { matchesLocalSearch } from './localSearchMatch';

const row = {
  title: 'Krea2 walkthrough',
  author_nickname: 'blackcrane',
  description: 'comfyui notes',
  hashtags: '#upscale',
};

describe('matchesLocalSearch', () => {
  it('matches a field that is in scope', () => {
    expect(matchesLocalSearch(row, 'krea2', ['title'])).toBe(true);
  });

  it('does not match a field that is out of scope', () => {
    // The title contains the word, but the user unticked Title.
    expect(matchesLocalSearch(row, 'krea2', ['description'])).toBe(false);
  });

  it('reads tags from the supplied tag text only when tags are in scope', () => {
    expect(matchesLocalSearch(row, 'tutorial', ['tags'], 'tutorial ai')).toBe(
      true,
    );
    expect(matchesLocalSearch(row, 'tutorial', ['title'], 'tutorial ai')).toBe(
      false,
    );
  });

  it('matches the author under either field name', () => {
    expect(matchesLocalSearch(row, 'blackcrane', ['author'])).toBe(true);
    expect(
      matchesLocalSearch({ author: 'heygo' }, 'heygo', ['author']),
    ).toBe(true);
  });

  it('defers to the backend for a scope it cannot evaluate locally', () => {
    // notes / transcript live server-side. Filtering everything out here would
    // render the "no downloaded content yet" empty state, and for a
    // single-character query (which never reaches the backend) it would stay
    // that way. Showing the page unfiltered is the honest intermediate state.
    expect(matchesLocalSearch(row, 'krea2', ['notes', 'transcript'])).toBe(
      true,
    );
  });

  it('keeps the derived field list in step with the matcher', async () => {
    const { LOCALLY_MATCHABLE_FIELDS } = await import('./localSearchMatch');
    // Every advertised field must actually be able to match something, so the
    // constant cannot drift away from the matcher's behaviour.
    for (const field of LOCALLY_MATCHABLE_FIELDS) {
      const probe = {
        title: 'zzz',
        description: 'zzz',
        author_nickname: 'zzz',
        hashtags: 'zzz',
      };
      expect(matchesLocalSearch(probe, 'zzz', [field], 'zzz')).toBe(true);
    }
  });

  it('keeps every row when the query is blank', () => {
    expect(matchesLocalSearch(row, '   ', ['title'])).toBe(true);
  });

  it('is case insensitive', () => {
    expect(matchesLocalSearch(row, 'KREA2', ['title'])).toBe(true);
  });
});
