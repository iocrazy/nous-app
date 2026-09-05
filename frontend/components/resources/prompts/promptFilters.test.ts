import { describe, expect, it } from 'vitest';
import { parsePromptFilters, serializePromptFilters, sortEntries } from './promptFilters';

describe('promptFilters', () => {
  it('parses known values and drops unknown ones', () => {
    const f = parsePromptFilters(new URLSearchParams('form=album&origin=weird&project=55&sort=title&q=rain'));
    expect(f).toEqual({ form: 'album', origin: null, projectId: '55', sort: 'title', q: 'rain' });
  });
  it('round-trips and omits defaults', () => {
    const sp = serializePromptFilters({ form: null, origin: 'typed', projectId: null, sort: 'recent', q: '' });
    expect(sp.toString()).toBe('origin=typed');
  });
  it('sorts by title when asked and keeps server order otherwise', () => {
    const a = { title: 'b' } as never, b = { title: 'A' } as never;
    expect(sortEntries([a, b], 'title')).toEqual([b, a]);
    expect(sortEntries([a, b], 'recent')).toEqual([a, b]);
  });
});
