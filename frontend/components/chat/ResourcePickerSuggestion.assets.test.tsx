/**
 * The `@` picker's sixth tab: library ASSETS.
 *
 * Its own file rather than more cases in `ResourcePickerSuggestion.test.tsx`
 * because the tab is OPTIONAL — the canvas renders this same popover as a
 * positioning shell and passes no `assets` prop at all. Keeping the two apart
 * makes "the popover still works without the tab" a case somebody can read
 * rather than an absence they have to notice.
 *
 * The grid itself is covered in `components/assets/AssetGridPicker.test.tsx`;
 * what is under test here is the seam — which tab is lit, which body renders,
 * what the counts row says, and that switching tabs does not throw away the
 * query the user already typed.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

import { ResourcePickerSuggestion, type AssetsTabProps } from './ResourcePickerSuggestion';
import type { AssetGridRow } from '../assets/AssetGridPicker';
import type { ResourceSearchResult } from '../../types';

/** `/api/v1/resources/search` rows — bare body, relative cover path. */
const ROWS: ResourceSearchResult[] = [
  {
    id: '1', name: 'story.md', kind: 'doc', mime: 'text/markdown', size: 100,
    scope: { type: 'personal', id: 'u' }, updated_at: '2026-05-24T00:00:00Z',
    thumbnail_url: null, transcript_status: null, summary_status: null,
  },
];
const COUNTS = { all: 1, video: 0, image: 0, doc: 1, audio: 0, pdf: 0 };

/** `GET /api/v1/assets/search` rows — Envelope-unwrapped, every id a STRING
 *  (`assets_repository._serialize` calls `str()` on every BIGINT column). */
const AVA: AssetGridRow = {
  id: '727145299382534201',
  name: 'Ava',
  asset_type: 'character',
  cover_file_id: '727145299382534301',
  readiness: { state: 'ready', missing: [] },
  scope_id: '727145299382534200',
};

function makeAssets(over: Partial<AssetsTabProps> = {}): AssetsTabProps {
  return {
    active: false,
    onActivate: vi.fn(),
    count: 0,
    onCountChange: vi.fn(),
    onSelect: vi.fn(),
    fetch: vi.fn(async () => [AVA]),
    ...over,
  };
}

function renderPicker(
  assets: AssetsTabProps | undefined,
  props: Partial<React.ComponentProps<typeof ResourcePickerSuggestion>> = {},
) {
  return render(
    <ResourcePickerSuggestion
      items={ROWS}
      query=""
      loading={false}
      counts={COUNTS}
      activeKind=""
      onKindChange={vi.fn()}
      onSelect={vi.fn()}
      assets={assets}
      {...props}
    />,
  );
}

beforeEach(() => {
  vi.stubEnv('VITE_API_URL', 'https://api.example.test');
});
afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('the Assets tab is opt-in', () => {
  it('is absent when the caller passes no assets prop', () => {
    renderPicker(undefined);
    expect(screen.queryByTestId('resource-picker-tab-assets')).toBeNull();
    expect(screen.getByTestId('resource-picker-list')).toBeInTheDocument();
  });

  it('sits after Doc, so it does not read as a sixth file kind', () => {
    renderPicker(makeAssets());
    const kinds = [...screen.getByTestId('resource-picker-tabs').children].map((el) =>
      el.getAttribute('data-kind'),
    );
    expect(kinds).toEqual(['all', 'video', 'audio', 'image', 'doc', 'assets']);
  });

  it('shows the asset count on the tab', () => {
    renderPicker(makeAssets({ count: 7 }));
    expect(screen.getByTestId('resource-picker-tab-assets')).toHaveTextContent('7');
  });

  it('badges no number at all before anybody has asked', () => {
    // `count: null` is "nobody asked", and it must not render as 0 — an
    // unvisited tab reading "Assets 0" states that the library is empty, which
    // is a claim no request supports.
    renderPicker(makeAssets({ count: null }));
    expect(screen.getByTestId('resource-picker-tab-assets').textContent?.trim()).toBe(
      'Assets',
    );
  });

  it('asks nothing until its tab is opened', async () => {
    // The grid is mounted from the start (so the type chip survives a switch)
    // but must stay silent: `GET /assets/search` costs four round trips.
    const fetch = vi.fn(async () => [AVA]);
    renderPicker(makeAssets({ fetch }));
    await new Promise((r) => setTimeout(r, 0));
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.queryByTestId('mention-asset-option')).toBeNull();
  });

  it('asks the parent to activate it rather than deciding for itself', () => {
    const onActivate = vi.fn();
    renderPicker(makeAssets({ onActivate }));
    fireEvent.click(screen.getByTestId('resource-picker-tab-assets'));
    expect(onActivate).toHaveBeenCalled();
  });
});

describe('when the Assets tab is active', () => {
  it('renders the asset grid instead of the resource list', async () => {
    renderPicker(makeAssets({ active: true }));
    await screen.findAllByTestId('mention-asset-option');
    expect(screen.queryByTestId('resource-picker-list')).toBeNull();
  });

  it('does not show the resource empty state over an asset list', async () => {
    // The two lists have different empty states, and the resource one names
    // the query ("No resources match …"). Leaking it under the asset grid
    // would tell the user their search failed while it was showing results.
    renderPicker(makeAssets({ active: true }), { items: [], query: 'av' });
    await screen.findAllByTestId('mention-asset-option');
    expect(screen.queryByText(/no resources match|chat\.mentionPicker\.noResults/i)).toBeNull();
  });

  it('lights only the Assets tab, never a resource kind as well', async () => {
    renderPicker(makeAssets({ active: true }), { activeKind: '' });
    await screen.findAllByTestId('mention-asset-option');
    const all = screen.getByTestId('resource-picker-tabs').querySelector('[data-kind="all"]')!;
    expect(all.className).not.toContain('accent-soft');
    expect(screen.getByTestId('resource-picker-tab-assets').className).toContain('accent-soft');
  });

  it('counts assets in the footer, not resources', async () => {
    renderPicker(makeAssets({ active: true, count: 3 }));
    await screen.findAllByTestId('mention-asset-option');
    const footer = screen.getByTestId('resource-picker-count');
    expect(footer.textContent).toContain('3');
    expect(footer.textContent).not.toContain('of 1');
  });

  it('leaves the footer blank while the first search is still out', async () => {
    renderPicker(makeAssets({ active: true, count: null }));
    await screen.findAllByTestId('mention-asset-option');
    expect(screen.getByTestId('resource-picker-count').textContent?.trim()).toBe('');
  });

  it('keeps the typed query when the tab is switched on', async () => {
    // The query lives in the composer, not in the tab, so a switch must not
    // restart the search from empty — the user typed "@av" once.
    const fetch = vi.fn(async () => [AVA]);
    renderPicker(makeAssets({ active: true, fetch }), { query: 'av' });
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        expect.objectContaining({ q: 'av' }),
        expect.anything(),
      ),
    );
  });

  it('hands the parent the row it picked', async () => {
    const onSelect = vi.fn();
    renderPicker(makeAssets({ active: true, onSelect }));
    fireEvent.mouseDown((await screen.findAllByTestId('mention-asset-option'))[0]);
    expect(onSelect).toHaveBeenCalledWith(AVA);
  });

  it('asks the membership-wide endpoint through the injected transport only', async () => {
    // The popover knows nothing about scopes. A picker that built its own
    // request would be a second place for the authorization question to be
    // answered — and the two answers would drift.
    const fetch = vi.fn(async () => [AVA]);
    renderPicker(makeAssets({ active: true, fetch }));
    await screen.findAllByTestId('mention-asset-option');
    expect(fetch).toHaveBeenCalledWith(
      expect.objectContaining({ library: 'all', limit: 24 }),
      expect.any(AbortSignal),
    );
  });
});

describe('the resource tabs are unchanged by the addition', () => {
  it('still renders rows and still selects them', () => {
    const onSelect = vi.fn();
    renderPicker(makeAssets(), { onSelect });
    fireEvent.click(screen.getByText('story.md').closest('button')!);
    expect(onSelect).toHaveBeenCalledWith(ROWS[0]);
  });

  it('still reports "N of all" in the footer', () => {
    renderPicker(makeAssets());
    expect(screen.getByTestId('resource-picker-count').textContent).toBe('1 of 1');
  });
});

describe('the keyboard hint claims only what is true', () => {
  // `AIChatPanel.handleMentionKey` returns false unless the Assets tab is
  // active, so on the five resource tabs the arrows do nothing and Enter falls
  // through to send. The footer promised `↑↓ navigate · ↵ insert` on all six
  // (final review M4). It now appears only where it holds.
  const HINT = 'chat.mentionPicker.hintKbd';

  it('is shown on the Assets tab', async () => {
    renderPicker(makeAssets({ active: true }));
    await screen.findAllByTestId('mention-asset-option');
    expect(screen.getByText(HINT)).toBeInTheDocument();
  });

  it('is absent on a resource tab', () => {
    renderPicker(makeAssets({ active: false }));
    expect(screen.queryByText(HINT)).toBeNull();
  });

  it('is absent when there is no Assets tab at all', () => {
    renderPicker(undefined);
    expect(screen.queryByText(HINT)).toBeNull();
  });
});
