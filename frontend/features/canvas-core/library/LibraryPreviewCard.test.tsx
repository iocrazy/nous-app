// features/canvas-core/library/LibraryPreviewCard.test.tsx
//
// The hover preview. What these cases pin: it stays away until the user has
// really settled on a cell (a card that appears on every pointer crossing is
// a flicker, not a preview); once shown it answers spec §4 item 7 — with
// `max_refs` 3 over four files, three rows Send and one is Cut; a failed
// detail fetch says so instead of drawing an empty table, which would read as
// "this asset has no files"; and a non-asset gets the picture and no table at
// all, because "which files are sent" is not a question an upload has.

import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) => {
      const fill = (s: string, vars: Record<string, unknown>) =>
        s.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => String(vars[name] ?? ''));
      if (typeof d === 'string') return d;
      if (typeof d === 'object' && d !== null && 'defaultValue' in (d as object)) {
        const o = d as Record<string, unknown> & { defaultValue: string };
        return fill(String(o.defaultValue), o);
      }
      return k;
    },
  }),
}));

const fetchAssetDetail = vi.fn();
const listGenerationCapabilities = vi.fn();

vi.mock('../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchAssetDetail: (...a: unknown[]) => fetchAssetDetail(...a),
}));
vi.mock('../services/canvasGenerationService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  listGenerationCapabilities: () => listGenerationCapabilities(),
}));

import { _resetModelCapabilitiesCache } from '../smart/nodes/useModelCapabilities';
import { LibraryPreviewCard, PREVIEW_DELAY_MS } from './LibraryPreviewCard';
import type { LibraryItem } from './librarySearch';

const SCOPE = '727145299382534100';
const MODEL = 'seedream-4.0';

const ASSET: LibraryItem = {
  store: 'assets',
  id: '727145299382534300',
  title: 'Cole Bannon',
  thumbUrl: '/api/v1/resources/600000000000000001/cover',
  kind: 'character',
  ready: true,
};

const UPLOAD: LibraryItem = {
  store: 'uploads',
  id: '655000000000000001',
  title: 'harbour.png',
  thumbUrl: '/api/v1/resources/655000000000000001/cover',
  kind: 'image',
};

/** One `asset_files` row — the real wire shape, every id a string. */
const file = (rid: string, slot: string, sort: number) => ({
  asset_id: ASSET.id,
  resource_id: rid,
  slot,
  loadout_id: null,
  sort_order: sort,
  note: null,
  attached_by: null,
  attached_at: '2026-09-01T00:00:00Z',
});

/** `GET /assets/{id}` in full — the shape `addReferences.test.ts` uses, with
 *  the four-file list `referenceCut.test.ts` is written against. */
const ASSET_DETAIL = {
  id: ASSET.id,
  scope_id: SCOPE,
  asset_type: 'character' as const,
  subtype: null,
  name: 'Cole Bannon',
  role_tag: 'lead',
  description: '',
  attrs: {},
  prompt_positive: null,
  prompt_negative: null,
  prompt_positive_zh: null,
  prompt_negative_zh: null,
  platform_params: {},
  cover_file_id: '600000000000000001',
  source: 'manual',
  duplicated_from: null,
  is_system_preset: false,
  in_library: true,
  tags: {},
  sort_order: 0,
  created_by: null,
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
  readiness: { state: 'ready' as const, missing: [] },
  file_counts_by_slot: { sheet: 4 },
  project_ids: [],
  loadout_count: 0,
  // Four files in the PRIMARY slot, which is the population a pick really
  // delivers (`primarySlotFileIds`) — so spec §4 item 7's "4 files, 3 sent" is
  // a statement about this asset rather than about its whole attachment list.
  files: [
    file('600000000000000001', 'sheet', 0),
    file('600000000000000002', 'sheet', 1),
    file('600000000000000003', 'sheet', 2),
    file('600000000000000004', 'sheet', 3),
  ],
  links: [],
  linked_by: [],
  loadouts: [],
};

/** The same asset with two of its files in slots a pick leaves behind. */
const MIXED_DETAIL = {
  ...ASSET_DETAIL,
  file_counts_by_slot: { sheet: 2, stills: 1, worn: 1 },
  files: [
    file('600000000000000001', 'sheet', 0),
    file('600000000000000002', 'sheet', 1),
    file('600000000000000004', 'stills', 0),
    file('600000000000000003', 'worn', 0),
  ],
};

/** The hovered cell, in viewport coordinates. */
const rectAt = (left: number, top: number): DOMRect =>
  ({
    x: left,
    y: top,
    width: 120,
    height: 96,
    left,
    top,
    right: left + 120,
    bottom: top + 96,
    toJSON: () => ({}),
  }) as DOMRect;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  _resetModelCapabilitiesCache();
  fetchAssetDetail.mockReset();
  listGenerationCapabilities.mockReset();
  fetchAssetDetail.mockResolvedValue(ASSET_DETAIL);
  listGenerationCapabilities.mockResolvedValue({
    [MODEL]: {
      ratios: ['1:1'],
      quality: false,
      resolution: false,
      max_refs: 3,
      negative: false,
      video_modes: [],
    },
  });
  Object.defineProperty(window, 'innerWidth', { value: 1440, writable: true, configurable: true });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function settle(): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(PREVIEW_DELAY_MS);
  });
}

describe('LibraryPreviewCard', () => {
  it('draws nothing until the pointer has really settled', async () => {
    render(
      <LibraryPreviewCard item={ASSET} anchor={rectAt(200, 120)} model={MODEL} scopeId={SCOPE} />,
    );

    expect(screen.queryByTestId('library-preview')).toBeNull();
    await act(async () => {
      vi.advanceTimersByTime(PREVIEW_DELAY_MS - 1);
    });
    expect(screen.queryByTestId('library-preview')).toBeNull();
    // Nothing is fetched either — a preview that requests on every crossing
    // would hammer the detail endpoint for cards nobody looked at.
    expect(fetchAssetDetail).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    await waitFor(() => expect(screen.getByTestId('library-preview')).toBeInTheDocument());
  });

  it('spec §4 item 7 — max_refs 3 over four files gives three Sends and one Cut', async () => {
    render(
      <LibraryPreviewCard item={ASSET} anchor={rectAt(200, 120)} model={MODEL} scopeId={SCOPE} />,
    );
    await settle();

    const rows = await waitFor(() => {
      const found = screen.getAllByTestId('library-preview-row');
      expect(found).toHaveLength(4);
      return found;
    });
    expect(rows.map((r) => r.getAttribute('data-sends'))).toEqual([
      'true',
      'true',
      'true',
      'false',
    ]);
    // Every row is the primary slot, because that is what a pick sends.
    expect(rows.map((r) => r.getAttribute('data-slot'))).toEqual([
      'sheet',
      'sheet',
      'sheet',
      'sheet',
    ]);
    expect(fetchAssetDetail).toHaveBeenCalledWith(SCOPE, ASSET.id);
  });

  it('says what a pick leaves behind, so a short table is not a silent one', async () => {
    fetchAssetDetail.mockResolvedValue(MIXED_DETAIL);
    render(
      <LibraryPreviewCard item={ASSET} anchor={rectAt(200, 120)} model={MODEL} scopeId={SCOPE} />,
    );
    await settle();

    // Two rows, not four: `worn` and `stills` are not what a pick delivers.
    const rows = await waitFor(() => {
      const found = screen.getAllByTestId('library-preview-row');
      expect(found).toHaveLength(2);
      return found;
    });
    expect(rows.map((r) => r.getAttribute('data-sends'))).toEqual(['true', 'true']);
    expect(await screen.findByTestId('library-preview-other-slots')).toHaveTextContent(
      '2 files in other slots are not sent by default',
    );
  });

  it('stays quiet about other slots when there are none', async () => {
    render(
      <LibraryPreviewCard item={ASSET} anchor={rectAt(200, 120)} model={MODEL} scopeId={SCOPE} />,
    );
    await settle();

    await waitFor(() => expect(screen.getAllByTestId('library-preview-row')).toHaveLength(4));
    expect(screen.queryByTestId('library-preview-other-slots')).toBeNull();
  });

  it('names the model and the ceiling, so the trim is attributable', async () => {
    render(
      <LibraryPreviewCard item={ASSET} anchor={rectAt(200, 120)} model={MODEL} scopeId={SCOPE} />,
    );
    await settle();

    const note = await screen.findByTestId('library-preview-note');
    expect(note).toHaveTextContent(`On ${MODEL}, 3 references are sent.`);
  });

  it('with no target node it says so rather than inventing a ceiling', async () => {
    render(
      <LibraryPreviewCard item={ASSET} anchor={rectAt(200, 120)} model={null} scopeId={SCOPE} />,
    );
    await settle();

    const note = await screen.findByTestId('library-preview-note');
    expect(note).toHaveTextContent('Pick a target node to see which files are sent.');
    // An unknown ceiling sends everything — never zero.
    const rows = await waitFor(() => screen.getAllByTestId('library-preview-row'));
    expect(rows.map((r) => r.getAttribute('data-sends'))).toEqual([
      'true',
      'true',
      'true',
      'true',
    ]);
  });

  it('a failed detail fetch says so instead of drawing an empty table', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    fetchAssetDetail.mockRejectedValue(new Error('boom'));
    render(
      <LibraryPreviewCard item={ASSET} anchor={rectAt(200, 120)} model={MODEL} scopeId={SCOPE} />,
    );
    await settle();

    await waitFor(() =>
      expect(screen.getByTestId('library-preview-error')).toBeInTheDocument(),
    );
    expect(screen.queryAllByTestId('library-preview-row')).toHaveLength(0);
    err.mockRestore();
  });

  it('a non-asset gets the picture and no file table', async () => {
    render(
      <LibraryPreviewCard item={UPLOAD} anchor={rectAt(200, 120)} model={MODEL} scopeId={SCOPE} />,
    );
    await settle();

    const card = await waitFor(() => screen.getByTestId('library-preview'));
    expect(card.querySelector('img')).toHaveAttribute('src', UPLOAD.thumbUrl);
    expect(screen.queryAllByTestId('library-preview-row')).toHaveLength(0);
    expect(screen.queryByTestId('library-preview-note')).toBeNull();
    expect(fetchAssetDetail).not.toHaveBeenCalled();
  });

  it('sits beside the cell, and flips to its left when the card would run off', async () => {
    const { rerender } = render(
      <LibraryPreviewCard item={UPLOAD} anchor={rectAt(200, 120)} model={null} scopeId={SCOPE} />,
    );
    await settle();

    const card = await waitFor(() => screen.getByTestId('library-preview'));
    expect(card.style.left).toBe('328px'); // 200 + 120 + 8
    expect(card.style.top).toBe('120px');

    // A cell hugging the right edge: 1360 + 120 + 320 > 1440.
    rerender(
      <LibraryPreviewCard item={UPLOAD} anchor={rectAt(1360, 120)} model={null} scopeId={SCOPE} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId('library-preview').style.left).toBe('1032px'),
    ); // 1360 - 320 - 8
  });

  it('never eats the pointer — the cell under it keeps the hover', async () => {
    render(
      <LibraryPreviewCard item={UPLOAD} anchor={rectAt(200, 120)} model={null} scopeId={SCOPE} />,
    );
    await settle();

    const card = await waitFor(() => screen.getByTestId('library-preview'));
    expect(card.className).toContain('pointer-events-none');
  });
});
