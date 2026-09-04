/**
 * What switching tabs must NOT take away.
 *
 * `PromptNodeView.mention.test.tsx` drives this picker through the node and
 * covers what a pick does. This file covers the seam the shared-grid
 * extraction put at risk: the Assets grid now lives in
 * `components/assets/AssetGridPicker.tsx`, and rendering it only while its tab
 * is open would reset a search the user refined by hand and the type chip they
 * picked — silently, on a glance at the other tab. The grid is therefore
 * mounted-but-inactive, and this pins the host passing that through.
 *
 * The other half of the same seam: an inactive grid must still ask NOTHING.
 * Keeping it mounted is worth nothing if it costs four round trips per
 * keystroke on a list nobody is reading.
 *
 * Since #2102 the palette has FOUR groups, and the search term is shared by
 * all of them — so the Assets box is CONTROLLED by this file. That makes the
 * "survives a tab trip" case below stronger than it was: the term now has to
 * survive a round trip through the parent, not merely sit still in a component
 * nobody unmounted.
 *
 * The Uploads / Generated groups belong to `PromptMentionPicker.library.test`;
 * their services are stubbed here only so mounting this component touches no
 * network, and the layout stubs are what let the justified grid measure
 * anything at all in jsdom.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      typeof d === 'string'
        ? d
        : typeof d === 'object' && d !== null && 'defaultValue' in d
          ? String((d as { defaultValue: unknown }).defaultValue)
          : k,
  }),
}));

const searchAssets = vi.fn();
const searchResources = vi.fn();
const fetchGenerated = vi.fn();
// Spread the real modules: a whole-module factory silently drops every export
// it does not list, and this file mounts a component that imports several.
vi.mock('../../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  searchAssets: (...a: unknown[]) => searchAssets(...a),
}));
vi.mock('../../../../services/resourceSearchService', () => ({
  searchResources: (...a: unknown[]) => searchResources(...a),
}));
vi.mock('../../../../services/generatedService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchGenerated: (...a: unknown[]) => fetchGenerated(...a),
}));

class ObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): [] {
    return [];
  }
}

import { PromptMentionPicker } from './PromptMentionPicker';
import type { AssetSummary } from '../../../../services/assetsService';

const SCOPE = '727145299382534100';
const CANVAS = '900000000000000001';

/** Wire rows: string ids, `readiness` derived server-side. */
const AVA: AssetSummary = {
  id: '727145299382534201',
  scope_id: SCOPE,
  asset_type: 'character',
  name: 'Ava',
  role_tag: 'lead',
  readiness: { state: 'ready', missing: [] },
  cover_file_id: '727145299382534301',
  is_system_preset: false,
};

const IMAGES = [
  { url: '/api/v1/generated-media/7/cover', label: 'Image 1' },
  { url: '/api/v1/generated-media/8/cover', label: 'Image 2' },
];

function renderPicker(onPickAsset = vi.fn()) {
  render(
    <PromptMentionPicker
      scopeId={SCOPE}
      canvasId={CANVAS}
      inputImages={IMAGES}
      onPickImage={vi.fn()}
      onPickAsset={onPickAsset}
      onPickLibraryImage={vi.fn()}
      query=""
    />,
  );
  return onPickAsset;
}

let realRect: () => DOMRect;
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv('VITE_API_URL', 'https://api.example.test');
  // Classes, not arrow-function mocks: the virtualizer calls
  // `new ResizeObserver(…)`, which vitest 4 cannot construct from one.
  global.ResizeObserver = ObserverStub as unknown as typeof ResizeObserver;
  global.IntersectionObserver = ObserverStub as unknown as typeof IntersectionObserver;
  realRect = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function () {
    return {
      width: 640, height: 480, top: 0, left: 0, right: 640, bottom: 480,
      x: 0, y: 0, toJSON: () => ({}),
    } as DOMRect;
  };
  searchAssets.mockResolvedValue([AVA]);
  searchResources.mockResolvedValue({
    results: [],
    counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
    next_cursor: null,
  });
  fetchGenerated.mockResolvedValue({ items: [], next_cursor: null });
});
afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = realRect;
  cleanup();
  vi.unstubAllEnvs();
});

describe('PromptMentionPicker — the Assets grid across a tab switch', () => {
  it('asks nothing while the Input tab is showing', async () => {
    // With input images present the picker opens on Input. A grid that queried
    // from behind the hidden tab would spend the search budget on a list that
    // is not on screen.
    renderPicker();
    expect(screen.getByTestId('mention-tab-input')).toHaveAttribute('aria-pressed', 'true');
    await screen.findAllByTestId('mention-input-option');
    await new Promise((r) => setTimeout(r, 0));
    expect(searchAssets).not.toHaveBeenCalled();
    expect(screen.queryByTestId('mention-search')).toBeNull();
  });

  it('shares one search term with the rest of the palette', async () => {
    // #2102 put one box in one place for all four groups. An Assets box with
    // its own state would be a second search term in the same popover — type
    // in one, switch group, and the other still shows the old words.
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-assets'));
    await screen.findAllByTestId('mention-asset-option');
    fireEvent.change(screen.getByTestId('mention-search'), { target: { value: 'ava' } });

    fireEvent.click(screen.getByTestId('mention-tab-uploads'));
    const libraryBox = await screen.findByPlaceholderText('Search Library…');
    expect((libraryBox as HTMLInputElement).value).toBe('ava');
  });

  it('keeps a hand-typed search term when the user glances at the Input tab', async () => {
    renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-assets'));
    await screen.findAllByTestId('mention-asset-option');

    fireEvent.change(screen.getByTestId('mention-search'), { target: { value: 'ava' } });
    await waitFor(() =>
      expect(searchAssets).toHaveBeenLastCalledWith(
        SCOPE,
        expect.objectContaining({ q: 'ava' }),
      ),
    );

    // The Input group filters by that SAME term, so with "ava" typed it
    // legitimately has nothing to show — that is the shared-term contract
    // working, not a missing list.
    fireEvent.click(screen.getByTestId('mention-tab-input'));
    expect(await screen.findByTestId('mention-input-empty')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('mention-tab-assets'));

    const box = (await screen.findByTestId('mention-search')) as HTMLInputElement;
    expect(box.value).toBe('ava');
    await waitFor(() =>
      expect(searchAssets).toHaveBeenLastCalledWith(
        SCOPE,
        expect.objectContaining({ q: 'ava' }),
      ),
    );
  });

  it('counts the tab that is actually showing', async () => {
    // Two input images, one asset — a footer reading the wrong list is the
    // kind of thing only a count that moves can catch.
    renderPicker();
    await screen.findAllByTestId('mention-input-option');
    expect(screen.getByTestId('mention-count').textContent).toBe('2 results');

    fireEvent.click(screen.getByTestId('mention-tab-assets'));
    await screen.findAllByTestId('mention-asset-option');
    await waitFor(() =>
      expect(screen.getByTestId('mention-count').textContent).toBe('1 results'),
    );
  });

  it('hands the pick straight through, with no cast at the seam', async () => {
    const onPickAsset = renderPicker();
    fireEvent.click(screen.getByTestId('mention-tab-assets'));
    const option = (await screen.findAllByTestId('mention-asset-option'))[0];
    fireEvent.mouseDown(option);
    expect(onPickAsset).toHaveBeenCalledWith(AVA);
  });
});
