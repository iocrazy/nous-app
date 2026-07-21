import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Mock only the HTTP boundary + auth/url helpers so the real request-building
// and response-parsing is exercised. Mirrors gallery.test.ts.
vi.mock('./parserService', () => ({
  getAuthHeaders: vi.fn().mockResolvedValue({ Authorization: 'Bearer test' }),
}));
vi.mock('../utils/apiConfig', () => ({
  getApiUrl: () => 'https://api.test',
}));

import type { ResourceItem } from '../types';
import {
  applyGalleryMembership,
  fetchGalleryScopeMembership,
  invalidateGalleryMembership,
  type GalleryScopeMembership,
} from './resourceService';

function okFetch(body: unknown) {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as unknown as Response));
}

/** Minimal resource_items-shaped row: top-level id is the JOIN row id, the
 *  embedded resource carries the real resource id + mime + gallery_count. */
function row(resourceId: string, mime = 'image/jpeg'): ResourceItem {
  return {
    id: `item-${resourceId}`,
    resource: { id: resourceId, mime_type: mime },
  } as unknown as ResourceItem;
}

const GALLERY_MIME = 'application/x-mediahub-gallery';

describe('fetchGalleryScopeMembership — HTTP + parsing', () => {
  beforeEach(() => {
    invalidateGalleryMembership();
    vi.clearAllMocks();
  });
  afterEach(() => vi.unstubAllGlobals());

  it('GETs /resources/gallery-membership with scope_id and parses the payload', async () => {
    const fn = okFetch({
      success: true,
      data: {
        child_image_ids: ['11', '22'],
        gallery_counts: { '99': 3, '77': 1 },
      },
    });
    vi.stubGlobal('fetch', fn);

    const m = await fetchGalleryScopeMembership('scope-1');

    const url = (fn.mock.calls[0] as unknown[])[0] as string;
    expect(url).toContain('/api/v1/resources/gallery-membership?');
    expect(url).toContain('scope_id=scope-1');
    expect(m.childIds).toEqual(new Set(['11', '22']));
    expect(m.counts.get('99')).toBe(3);
    expect(m.counts.get('77')).toBe(1);
  });

  it('returns empty membership (no request) for a missing scope', async () => {
    const fn = okFetch({});
    vi.stubGlobal('fetch', fn);
    const m = await fetchGalleryScopeMembership(undefined);
    expect(m.childIds.size).toBe(0);
    expect(fn).not.toHaveBeenCalled();
  });

  it('caches per scope and refetches after invalidation', async () => {
    const fn = okFetch({
      success: true,
      data: { child_image_ids: ['5'], gallery_counts: {} },
    });
    vi.stubGlobal('fetch', fn);

    await fetchGalleryScopeMembership('scope-2');
    await fetchGalleryScopeMembership('scope-2'); // cache hit
    expect(fn).toHaveBeenCalledTimes(1);

    invalidateGalleryMembership('scope-2');
    await fetchGalleryScopeMembership('scope-2'); // refetch
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('fails open (empty membership) on a non-ok response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) } as unknown as Response)),
    );
    const m = await fetchGalleryScopeMembership('scope-err');
    expect(m.childIds.size).toBe(0);
    expect(m.counts.size).toBe(0);
  });
});

describe('applyGalleryMembership — hide children + stamp counts', () => {
  const membership: GalleryScopeMembership = {
    childIds: new Set(['c1', 'c2']),
    counts: new Map([['g1', 3]]),
  };

  it('drops child rows and annotates gallery rows with their count', () => {
    const rows = [
      row('g1', GALLERY_MIME), // gallery → keep + gallery_count = 3
      row('c1'), // child → hidden
      row('plain'), // normal → keep untouched
      row('c2'), // child → hidden
    ];
    const out = applyGalleryMembership(rows, membership);

    expect(out.map((r) => r.resource?.id)).toEqual(['g1', 'plain']);
    expect(out[0].resource?.gallery_count).toBe(3);
    expect(out[1].resource?.gallery_count).toBeUndefined();
  });

  it('does not mutate the input rows (immutability)', () => {
    const rows = [row('g1', GALLERY_MIME)];
    const out = applyGalleryMembership(rows, membership);
    expect(rows[0].resource?.gallery_count).toBeUndefined(); // original untouched
    expect(out[0]).not.toBe(rows[0]); // fresh copy
    expect(out[0].resource?.gallery_count).toBe(3);
  });

  it('returns rows unchanged when membership is empty', () => {
    const empty: GalleryScopeMembership = { childIds: new Set(), counts: new Map() };
    const rows = [row('a'), row('b')];
    expect(applyGalleryMembership(rows, empty)).toBe(rows);
  });
});
