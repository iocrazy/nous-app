/**
 * Unit tests for nodeSurface — the surface → view-set mapping (B5, T-B5.2).
 *
 * Pins the conservative degradation contract (missing/null surface →
 * deliverable-only) and the per-surface view sets consumed by T-B5.4 / T-B5.6.
 */

import { describe, expect, it } from 'vitest';
import {
  SURFACE_VIEWS,
  isDeliverableOnly,
  resolveSurface,
  viewsForNode,
} from './nodeSurface';

describe('resolveSurface', () => {
  it('returns the surface for each non-null surface', () => {
    expect(resolveSurface({ surface: 'script' })).toBe('script');
    expect(resolveSurface({ surface: 'storyboard' })).toBe('storyboard');
    expect(resolveSurface({ surface: 'renders' })).toBe('renders');
  });

  it('returns null for an explicit null surface', () => {
    expect(resolveSurface({ surface: null })).toBeNull();
  });

  it('degrades missing surface / undefined / null node to null', () => {
    expect(resolveSurface({})).toBeNull();
    expect(resolveSurface(undefined)).toBeNull();
    expect(resolveSurface(null)).toBeNull();
  });
});

describe('viewsForNode', () => {
  it('returns the script view set (2 views)', () => {
    const views = viewsForNode({ surface: 'script' });
    expect(views.map((v) => v.key)).toEqual(['script', 'beats']);
  });

  it('returns the storyboard view set (3 views)', () => {
    const views = viewsForNode({ surface: 'storyboard' });
    expect(views).toHaveLength(3);
    expect(views.map((v) => v.key)).toEqual(['storyboard', 'canvas', 'shotlist']);
  });

  it('returns the renders view set (1 view)', () => {
    expect(viewsForNode({ surface: 'renders' }).map((v) => v.key)).toEqual(['renders']);
  });

  it('returns [] for deliverable-only (null / missing) nodes', () => {
    expect(viewsForNode({ surface: null })).toEqual([]);
    expect(viewsForNode({})).toEqual([]);
    expect(viewsForNode(undefined)).toEqual([]);
    expect(viewsForNode(null)).toEqual([]);
  });

  it('every view carries an i18n labelKey', () => {
    for (const views of Object.values(SURFACE_VIEWS)) {
      for (const v of views) {
        expect(v.labelKey).toMatch(/^projects\.episodeViews\./);
      }
    }
  });
});

describe('isDeliverableOnly', () => {
  it('is true when surface is null / missing', () => {
    expect(isDeliverableOnly({ surface: null })).toBe(true);
    expect(isDeliverableOnly({})).toBe(true);
    expect(isDeliverableOnly(undefined)).toBe(true);
    expect(isDeliverableOnly(null)).toBe(true);
  });

  it('is false for any creative surface', () => {
    expect(isDeliverableOnly({ surface: 'script' })).toBe(false);
    expect(isDeliverableOnly({ surface: 'storyboard' })).toBe(false);
    expect(isDeliverableOnly({ surface: 'renders' })).toBe(false);
  });
});
