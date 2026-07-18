/**
 * authorLabel unit tests — the actor → chip-label resolver. Covers the two
 * client-side specials (current user, copilot), the server-resolved username,
 * and the short-id fallback.
 */
import { describe, expect, it } from 'vitest';
import { authorLabel } from '../versions/authorLabel';

const t = (k: string) => k;

describe('authorLabel', () => {
  it('returns null when no actor is recorded', () => {
    expect(authorLabel(null, { t })).toBeNull();
    expect(authorLabel(undefined, { t })).toBeNull();
  });

  it('labels the current user as "You"', () => {
    expect(authorLabel('u-me', { currentUserId: 'u-me', t })).toBe('editor.diffAuthorYou');
  });

  it('labels the copilot actor as the AI author', () => {
    expect(authorLabel('copilot', { t })).toBe('editor.diffAuthorAI');
  });

  it('prefers a server-resolved username', () => {
    expect(authorLabel('u-bob', { authors: { 'u-bob': 'Bob' }, t })).toBe('Bob');
  });

  it('falls back to a short id when the uuid is unresolved', () => {
    const uuid = '12345678-90ab-cdef-1234-567890abcdef';
    expect(authorLabel(uuid, { t })).toBe('12345678');
  });

  it('current user wins over a resolved username', () => {
    expect(
      authorLabel('u-me', { currentUserId: 'u-me', authors: { 'u-me': 'Me Name' }, t }),
    ).toBe('editor.diffAuthorYou');
  });
});
