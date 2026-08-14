/**
 * Character-name matching rules for the script-cast vs. library diff.
 * The server's unique index is byte-exact; these rules deliberately match
 * wider so near-duplicate imports are never suggested.
 */

import { describe, expect, it } from 'vitest';
import { missingCharacterNames, normalizeCharacterName } from './characterNameMatch';

describe('normalizeCharacterName', () => {
  it('trims, collapses inner whitespace, lowercases', () => {
    expect(normalizeCharacterName('  Ada   Byron ')).toBe('ada byron');
    expect(normalizeCharacterName('COLE')).toBe('cole');
  });

  it('folds fullwidth ASCII and the ideographic space to halfwidth', () => {
    expect(normalizeCharacterName('Ａda　Byron')).toBe('ada byron');
    expect(normalizeCharacterName('ＣＯＬＥ')).toBe('cole');
  });

  it('leaves CJK names untouched apart from surrounding whitespace', () => {
    expect(normalizeCharacterName(' 林小满 ')).toBe('林小满');
  });
});

describe('missingCharacterNames', () => {
  it('returns script names with no library card, in script order', () => {
    expect(missingCharacterNames(['Cole', 'Ada', 'Bram'], ['Cole'])).toEqual([
      'Ada',
      'Bram',
    ]);
  });

  it('is empty when every script name is already in the library', () => {
    expect(missingCharacterNames(['Cole', 'Ada'], ['Ada', 'Cole'])).toEqual([]);
  });

  it('does not re-import names differing only by trim / case / width', () => {
    expect(
      missingCharacterNames(['  COLE ', 'Ａda　Byron'], ['cole', 'Ada Byron']),
    ).toEqual([]);
  });

  it('de-dupes within the script list itself', () => {
    expect(missingCharacterNames(['Ada', 'ada', ' ADA '], [])).toEqual(['Ada']);
  });

  it('drops blank and whitespace-only names (server drops them too)', () => {
    expect(missingCharacterNames(['', '   ', 'Ada'], [])).toEqual(['Ada']);
    expect(missingCharacterNames(['Ada'], ['', '  '])).toEqual(['Ada']);
  });

  it('returns the trimmed script spelling, not the normalized key', () => {
    expect(missingCharacterNames(['  Ada Byron  '], [])).toEqual(['Ada Byron']);
  });

  it('is empty when the script has no cast at all', () => {
    expect(missingCharacterNames([], ['Cole'])).toEqual([]);
  });
});
