/**
 * railViewStorage — per-script rail-view persistence (canvas polish F2).
 *
 * Round-trips a stored view, rejects garbage back to null (so the caller falls
 * to its default), and degrades silently when localStorage throws.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readStoredRailView, persistRailView } from '../railViewStorage';

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('railViewStorage', () => {
  it('round-trips a persisted view under a per-script key', () => {
    persistRailView('42', 'nodes');
    expect(localStorage.getItem('editor.railView.42')).toBe('nodes');
    expect(readStoredRailView('42')).toBe('nodes');
  });

  it('keeps scripts isolated by id', () => {
    persistRailView('1', 'storyboard');
    expect(readStoredRailView('2')).toBeNull();
  });

  it('returns null for an unknown / invalid stored value', () => {
    expect(readStoredRailView('1')).toBeNull();
    localStorage.setItem('editor.railView.1', 'garbage');
    expect(readStoredRailView('1')).toBeNull();
  });

  it('degrades silently when localStorage.getItem throws', () => {
    vi.spyOn(localStorage, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(readStoredRailView('1')).toBeNull();
  });

  it('degrades silently when localStorage.setItem throws', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    expect(() => persistRailView('1', 'nodes')).not.toThrow();
  });
});
