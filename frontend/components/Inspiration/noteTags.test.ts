import { describe, expect, it } from 'vitest';
import { findActiveTag, parseTags } from './noteTags';

describe('parseTags — mirrors backend note_tags.py contract', () => {
  it('extracts in order', () => {
    expect(parseTags('idea #hooks and #formats now')).toEqual(['hooks', 'formats']);
  });
  it('dedups case-insensitively to lowercase', () => {
    expect(parseTags('#Hooks #hooks #HOOKS')).toEqual(['hooks']);
  });
  it('allows cjk, hyphen, underscore', () => {
    expect(parseTags('试试 #灵感 #short-form #a_b')).toEqual(['灵感', 'short-form', 'a_b']);
  });
  it('strips trailing punctuation via char-class boundary', () => {
    expect(parseTags('end #hooks. and (#formats)')).toEqual(['hooks', 'formats']);
  });
  it('ignores mid-word and url fragments', () => {
    expect(parseTags('c# is a language, see x.com/a#b')).toEqual([]);
  });
  it('ignores code fences and inline code', () => {
    const md = 'text #real\n```\n# comment not a tag\nfoo #fake\n```\n`inline #fake2`';
    expect(parseTags(md)).toEqual(['real']);
  });
  it('empty and markdown headings are not tags', () => {
    expect(parseTags('')).toEqual([]);
    expect(parseTags('# heading text')).toEqual([]);
  });
});

describe('findActiveTag — caret sits inside a #token being typed', () => {
  it('returns prefix while typing', () => {
    const text = 'idea #hoo';
    expect(findActiveTag(text, text.length)).toEqual({ start: 5, prefix: 'hoo' });
  });
  it('returns null when caret after space', () => {
    const text = 'idea #hooks ';
    expect(findActiveTag(text, text.length)).toBeNull();
  });
  it('returns null with no hash token', () => {
    expect(findActiveTag('plain', 5)).toBeNull();
  });
});
