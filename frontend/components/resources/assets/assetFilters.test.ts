/**
 * assetFilters — the URL is the shelf's state, so these are the rules that
 * decide what a pasted link reproduces and what request it produces.
 *
 * The two that matter most:
 *   * an unknown value is DROPPED, not forwarded (the server answers 422 on
 *     an enumeration it does not know, and the user reads a 422 as a broken
 *     page rather than a bad link they typed);
 *   * a default is OMITTED from both the URL and the request, so "not
 *     filtered" and "filtered to the default" are one state, not two.
 */
import { describe, expect, it } from 'vitest';

import {
  ASSET_PAGE_SIZE,
  DEFAULT_LIBRARY,
  DEFAULT_SORT,
  assetListOptionsFor,
  defaultAssetFilters,
  hasActiveAssetFilters,
  parseAssetFilters,
  serializeAssetFilters,
  tagOptionsFrom,
} from './assetFilters';

const parse = (qs: string) => parseAssetFilters(new URLSearchParams(qs));

describe('parseAssetFilters', () => {
  it('reads every filter off the query string', () => {
    expect(parse('project_id=55&readiness=draft&tag=lead&sort=name')).toEqual({
      projectId: '55',
      readiness: 'draft',
      // Absent from the URL, so it holds the default rather than being null:
      // `library` is the one filter with no "unset" state (mig 449).
      library: 'in',
      tag: 'lead',
      sort: 'name',
    });
  });

  it('DEFAULT_LIBRARY matches the value the SERVER defaults to', () => {
    // This coupling is load-bearing and easy to break silently. The shelf
    // OMITS `library` when it equals `DEFAULT_LIBRARY`, so the rows that come
    // back are decided by the server's own default (`in`, mig 449) no matter
    // what this constant says — while the chip's summary is decided by THIS
    // constant. Set them apart and the chip reads "All" over a grid holding
    // only library members, and nothing fails.
    //
    // The literal is deliberate: it mirrors `assets_router.list_assets`'s
    // `library: str = Query("in", ...)`. Changing the server default means
    // changing this line, which is the review the coupling deserves.
    expect(DEFAULT_LIBRARY).toBe('in');
    expect(defaultAssetFilters().library).toBe(DEFAULT_LIBRARY);
  });

  it('reads the library filter and drops a value the server would refuse', () => {
    expect(parse('library=out').library).toBe('out');
    expect(parse('library=all').library).toBe('all');
    // Dropped, not forwarded: `?library=maybe` is a 422 the user reads as a
    // broken page rather than as a bad link.
    expect(parse('library=maybe').library).toBe(DEFAULT_LIBRARY);
  });

  it('an empty URL is the default state', () => {
    expect(parse('')).toEqual(defaultAssetFilters());
    expect(parse('').sort).toBe(DEFAULT_SORT);
  });

  it('drops values the router would reject rather than forwarding them', () => {
    // `sort=cheapest` / `readiness=maybe` are 422s server-side. Forwarding
    // them would turn a hand-edited URL into what reads as a broken page.
    const filters = parse('readiness=maybe&sort=cheapest');
    expect(filters.readiness).toBeNull();
    expect(filters.sort).toBe(DEFAULT_SORT);
  });

  it('treats a present-but-empty param as absent', () => {
    // `?tag=` filters on the empty string if taken literally — a filter that
    // matches nothing while the chip claims to be active.
    expect(parse('tag=&project_id=%20%20')).toEqual(defaultAssetFilters());
  });
});

describe('serializeAssetFilters', () => {
  it('omits the defaults so a bare shelf has a bare URL', () => {
    expect(serializeAssetFilters(defaultAssetFilters()).toString()).toBe('');
  });

  it('round-trips a fully populated state', () => {
    const filters = {
      projectId: '55',
      readiness: 'ready',
      library: 'out',
      tag: 'lead',
      sort: 'readiness',
    } as const;
    expect(parseAssetFilters(serializeAssetFilters(filters))).toEqual(filters);
  });

  it('writes sort only when it is not the default', () => {
    expect(serializeAssetFilters({ ...defaultAssetFilters(), sort: 'name' }).get('sort')).toBe(
      'name',
    );
    expect(serializeAssetFilters({ ...defaultAssetFilters(), sort: 'recent' }).has('sort')).toBe(
      false,
    );
  });
});

describe('hasActiveAssetFilters', () => {
  it('is false for sort alone — re-ordering never removes a row', () => {
    // This drives the empty state's wording: a sorted-but-unfiltered empty
    // shelf really is empty, and saying "no assets match these filters"
    // there would send the user hunting for a filter to clear.
    expect(hasActiveAssetFilters({ ...defaultAssetFilters(), sort: 'name' })).toBe(false);
  });

  it.each([
    ['projectId', { projectId: '55' }],
    ['readiness', { readiness: 'draft' as const }],
    ['tag', { tag: 'lead' }],
  ])('is true when %s narrows the shelf', (_name, patch) => {
    expect(hasActiveAssetFilters({ ...defaultAssetFilters(), ...patch })).toBe(true);
  });

  it('counts library only as `out` — `in` is the default and `all` widens', () => {
    // Each value drives a different empty-state sentence. `in` empty is the
    // honest "you have no characters yet"; `all` empty cannot have been
    // narrowed by anything; only `out` is a filter the user set.
    expect(hasActiveAssetFilters({ ...defaultAssetFilters(), library: 'in' })).toBe(false);
    expect(hasActiveAssetFilters({ ...defaultAssetFilters(), library: 'all' })).toBe(false);
    expect(hasActiveAssetFilters({ ...defaultAssetFilters(), library: 'out' })).toBe(true);
  });
});

describe('assetListOptionsFor', () => {
  it('sends only what is set, plus the page window', () => {
    const opts = assetListOptionsFor(defaultAssetFilters(), null);
    expect(opts).toEqual({ limit: ASSET_PAGE_SIZE, offset: 0 });
    // Explicitly: no `type`, no `sort`, no nulls. `listAssets` drops
    // `undefined` keys, but a `null` would be sent as the string "null".
    expect('type' in opts).toBe(false);
    expect('sort' in opts).toBe(false);
  });

  it('carries the tab type and every active filter', () => {
    expect(
      assetListOptionsFor(
        { projectId: '55', readiness: 'draft', library: 'out', tag: 'lead', sort: 'name' },
        'character',
        60,
      ),
    ).toEqual({
      type: 'character',
      projectId: '55',
      readiness: 'draft',
      library: 'out',
      tag: 'lead',
      sort: 'name',
      limit: ASSET_PAGE_SIZE,
      offset: 60,
    });
  });

  it('omits library when it is the default, so the shelf sends the bare request', () => {
    // The server's own default is `in`. Sending it explicitly would produce
    // the identical query — and would put a param in the URL that says
    // nothing, which is the rule `sort` already follows.
    expect('library' in assetListOptionsFor(defaultAssetFilters(), 'character')).toBe(
      false,
    );
    expect(
      assetListOptionsFor({ ...defaultAssetFilters(), library: 'all' }, 'character').library,
    ).toBe('all');
  });

  it('keeps offset 0 rather than dropping it', () => {
    // 0 is a meaningful first page; only `undefined` means "not supplied".
    expect(assetListOptionsFor(defaultAssetFilters(), 'prop', 0).offset).toBe(0);
  });
});

describe('tagOptionsFrom', () => {
  // `tags` is a jsonb OBJECT of group → values, exactly as the wire fixture
  // shows (`{"role": ["lead"]}`) — not a flat array.
  const rows = [
    { tags: { role: ['lead', 'villain'] } },
    { tags: { role: ['lead'], era: ['tang'] } },
    { tags: {} },
  ];

  it('gathers every value across every group, deduped and sorted', () => {
    expect(tagOptionsFrom(rows, null)).toEqual(['lead', 'tang', 'villain']);
  });

  it('keeps the active value even when this page has no row carrying it', () => {
    // Otherwise a filter narrowed to empty offers no way back: the chip would
    // list only "Any Tag" and stop showing what it is filtering by.
    expect(tagOptionsFrom([], 'ghost')).toEqual(['ghost']);
  });

  it('accepts a scalar group value rather than dropping it', () => {
    expect(tagOptionsFrom([{ tags: { mood: 'grim' } }], null)).toEqual(['grim']);
  });

  it('ignores non-string values instead of rendering them', () => {
    expect(tagOptionsFrom([{ tags: { n: [1, null, 'ok'] } }], null)).toEqual(['ok']);
  });
});
