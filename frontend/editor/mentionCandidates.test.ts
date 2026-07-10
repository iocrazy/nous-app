import { describe, expect, it } from 'vitest';
import { mergeProjectMentionCandidates } from './mentionCandidates';

describe('mergeProjectMentionCandidates', () => {
  it('returns the script candidates unchanged when there are no project extras', () => {
    expect(mergeProjectMentionCandidates(['CLIENT', 'DEV'], [])).toEqual(['CLIENT', 'DEV']);
  });

  it('keeps script-candidate order first, then appends project extras alphabetically', () => {
    const result = mergeProjectMentionCandidates(
      ['DEV', 'CLIENT'],
      ['NARRATOR', 'ANNA'],
    );
    expect(result).toEqual(['DEV', 'CLIENT', 'ANNA', 'NARRATOR']);
  });

  it('dedupes case-insensitively, keeping the script-side casing', () => {
    const result = mergeProjectMentionCandidates(['Client'], ['CLIENT', 'DEV']);
    expect(result).toEqual(['Client', 'DEV']);
  });

  it('dedupes duplicate project candidates against each other', () => {
    const result = mergeProjectMentionCandidates([], ['Anna', 'ANNA', 'anna']);
    expect(result).toEqual(['Anna']);
  });

  it('ignores blank/whitespace-only project names', () => {
    const result = mergeProjectMentionCandidates(['CLIENT'], ['  ', '', 'DEV']);
    expect(result).toEqual(['CLIENT', 'DEV']);
  });

  it('returns an empty array when both inputs are empty', () => {
    expect(mergeProjectMentionCandidates([], [])).toEqual([]);
  });
});
