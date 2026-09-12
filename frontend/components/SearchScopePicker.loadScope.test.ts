import { beforeEach, describe, expect, it } from 'vitest';

import { loadSearchScope, saveSearchScope } from './SearchScopePicker';
import { DEFAULT_SEARCH_FIELDS } from '../services/searchService';

const V2 = 'mediahub_search_scope_v2';
const LEGACY = 'mediahub_search_scope';

describe('loadSearchScope', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('returns the default scope on a fresh install', () => {
    expect(loadSearchScope()).toEqual(DEFAULT_SEARCH_FIELDS);
  });

  it('includes tags and notes in the default, as the placeholder promises', () => {
    for (const promised of ['title', 'tags', 'notes']) {
      expect(loadSearchScope()).toContain(promised);
    }
  });

  it('upgrades a user who never touched the picker to the new default', () => {
    // Exactly the pre-v2 default = never customised.
    window.localStorage.setItem(
      LEGACY,
      JSON.stringify(['title', 'description', 'author', 'hashtags']),
    );
    expect(loadSearchScope()).toEqual(DEFAULT_SEARCH_FIELDS);
  });

  it('keeps a genuinely customised pre-v2 scope', () => {
    window.localStorage.setItem(LEGACY, JSON.stringify(['title']));
    expect(loadSearchScope()).toEqual(['title']);
  });

  it('prefers the v2 key once written', () => {
    window.localStorage.setItem(LEGACY, JSON.stringify(['title']));
    saveSearchScope(['tags', 'notes']);
    expect(loadSearchScope()).toEqual(['tags', 'notes']);
    expect(window.localStorage.getItem(V2)).toBe('["tags","notes"]');
  });

  it('drops unknown field names and falls back when nothing survives', () => {
    window.localStorage.setItem(V2, JSON.stringify(['bogus']));
    expect(loadSearchScope()).toEqual(DEFAULT_SEARCH_FIELDS);
  });

  it('survives malformed json', () => {
    window.localStorage.setItem(V2, '{not json');
    expect(loadSearchScope()).toEqual(DEFAULT_SEARCH_FIELDS);
  });

  it('self-heals a corrupt v2 value instead of rethrowing every load', () => {
    window.localStorage.setItem(V2, '{not json');
    loadSearchScope();
    expect(window.localStorage.getItem(V2)).toBeNull();
  });

  it('still migrates the legacy key when the v2 value is corrupt', () => {
    // A shared try/catch used to swallow the legacy read along with the bad
    // v2 parse, silently discarding a genuinely customised scope.
    window.localStorage.setItem(V2, '{not json');
    window.localStorage.setItem(LEGACY, JSON.stringify(['title']));
    expect(loadSearchScope()).toEqual(['title']);
  });

  it('retires the legacy key so the two can never diverge', () => {
    window.localStorage.setItem(LEGACY, JSON.stringify(['title']));
    loadSearchScope();
    expect(window.localStorage.getItem(LEGACY)).toBeNull();
    expect(window.localStorage.getItem(V2)).toBe('["title"]');
  });

  it('returns a fresh array so a mutating caller cannot poison the default', () => {
    const first = loadSearchScope();
    first.push('bogus-mutation' as never);
    expect(loadSearchScope()).toEqual(DEFAULT_SEARCH_FIELDS);
  });
});
