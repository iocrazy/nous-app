// features/canvas-core/library/LibraryReferencePopover.test.tsx
//
// The replacement for the Add-reference popover. What the old one could not
// do, and what these cases pin: SEARCH (its query was hard-coded to ''),
// MULTI-SELECT, and saying out loud how many references the model will take.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      typeof d === 'string'
        ? d
        : typeof d === 'object' && d !== null && 'defaultValue' in (d as object)
          ? String((d as { defaultValue: string }).defaultValue)
              .replace(/\{\{count\}\}/g, String((d as { count?: number }).count ?? ''))
              .replace(/\{\{model\}\}/g, String((d as { model?: string }).model ?? ''))
              .replace(/\{\{used\}\}/g, String((d as { used?: number }).used ?? ''))
              .replace(/\{\{max\}\}/g, String((d as { max?: number }).max ?? ''))
          : k,
  }),
}));

const searchResources = vi.fn();
const searchAssets = vi.fn();
const fetchGenerated = vi.fn();
const listGenerationCapabilities = vi.fn();

vi.mock('../../../services/resourceSearchService', () => ({
  searchResources: (...a: unknown[]) => searchResources(...a),
}));
vi.mock('../../../services/assetsService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  searchAssets: (...a: unknown[]) => searchAssets(...a),
}));
vi.mock('../../../services/generatedService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  fetchGenerated: (...a: unknown[]) => fetchGenerated(...a),
}));
vi.mock('../services/canvasGenerationService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  listGenerationCapabilities: () => listGenerationCapabilities(),
}));

import { _resetModelCapabilitiesCache } from '../smart/nodes/useModelCapabilities';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { LibraryReferencePopover } from './LibraryReferencePopover';

const SCOPE = '727145299382534100';
const UPLOAD_ROW = {
  id: '655000000000000001',
  name: 'harbour-dusk.png',
  kind: 'image' as const,
  mime: 'image/png',
  size: 1,
  scope: { type: 'team' as const, id: SCOPE },
  updated_at: '2026-09-01T10:11:12Z',
  thumbnail_url: '/api/v1/resources/655000000000000001/cover',
  transcript_status: null,
  summary_status: null,
};

// jsdom lays nothing out, so the justified pre-pass would see a container
// width of 0 and answer with no rows at all — every `library-cell` assertion
// below would then be pinning "the grid rendered nothing" rather than what it
// renders. Same stubs as LibraryGrid.test.tsx, for the same reason, plus the
// observers jsdom does not implement (absent, they warn and the run stops
// being pristine).
class ObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): [] {
    return [];
  }
}

let realRect: () => DOMRect;

function seed(manualRefs: Array<{ url: string; kind: string }> = []): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '900000000000000001',
    nodes: [
      {
        id: 'p1',
        type: 'prompt',
        position: { x: 0, y: 0 },
        data: {
          body: '',
          provider_slug: '',
          agent_id: null,
          run_status: 'idle',
          resource_refs: [],
          manual_refs: manualRefs,
          gen: { kind: 'image', model: 'doubao-seedream', ratio: '1:1', count: 1 },
        },
      },
    ] as never,
    connections: [],
    selection: [],
  });
}

function renderPopover() {
  return render(
    <MemoryRouter initialEntries={[`/team/${SCOPE}/canvas/900000000000000001`]}>
      <Routes>
        <Route
          path="/team/:teamId/canvas/:canvasId"
          element={
            <LibraryReferencePopover nodeId="p1" model="doubao-seedream" onClose={() => {}} />
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  global.ResizeObserver = ObserverStub as unknown as typeof ResizeObserver;
  global.IntersectionObserver = ObserverStub as unknown as typeof IntersectionObserver;
  realRect = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function () {
    return { width: 640, height: 480, top: 0, left: 0, right: 640, bottom: 480, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  };
  _resetModelCapabilitiesCache();
  seed();
  searchResources.mockReset().mockResolvedValue({
    results: [UPLOAD_ROW],
    counts: { all: 1, video: 0, image: 1, doc: 0, audio: 0, pdf: 0 },
    next_cursor: null,
  });
  searchAssets.mockReset().mockResolvedValue([]);
  fetchGenerated.mockReset().mockResolvedValue({ items: [], next_cursor: null });
  listGenerationCapabilities.mockReset().mockResolvedValue({
    'doubao-seedream': {
      ratios: ['1:1'],
      quality: false,
      resolution: false,
      max_refs: 3,
      negative: true,
      video_modes: [],
    },
  });
});
afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = realRect;
  cleanup();
  useCanvasCoreStore.getState().reset();
});

describe('LibraryReferencePopover', () => {
  it('HAS a search box, and typing narrows the request', async () => {
    renderPopover();
    await waitFor(() => expect(searchResources).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId('library-search'), {
      target: { value: 'harbour' },
    });
    await waitFor(() =>
      expect(searchResources).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: 'harbour' }),
      ),
    );
  });

  it('the header says which model receives them and how many it takes', async () => {
    renderPopover();
    await waitFor(() =>
      expect(screen.getByTestId('library-consequence').textContent).toContain(
        'sent to doubao-seedream',
      ),
    );
    expect(screen.getByTestId('library-consequence').textContent).toContain('0 / 3');
  });

  it('a full quota disables the button and says why', async () => {
    seed([
      { url: '/api/v1/generated-media/1/file', kind: 'image' },
      { url: '/api/v1/generated-media/2/file', kind: 'image' },
      { url: '/api/v1/generated-media/3/file', kind: 'image' },
    ]);
    renderPopover();
    await waitFor(() => expect(screen.getAllByTestId('library-cell').length).toBe(1));
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    expect(screen.getByTestId('library-primary')).toBeDisabled();
    expect(screen.getByTestId('library-note').textContent).toContain('3 / 3');
  });

  it('with unknown capabilities it falls back to the IC ceiling, never to zero', async () => {
    listGenerationCapabilities.mockRejectedValue(new Error('down'));
    renderPopover();
    await waitFor(() =>
      expect(screen.getByTestId('library-consequence').textContent).toContain('0 / 20'),
    );
    await waitFor(() => expect(screen.getAllByTestId('library-cell').length).toBe(1));
    fireEvent.click(screen.getAllByTestId('library-cell')[0]);
    expect(screen.getByTestId('library-primary')).not.toBeDisabled();
  });
});
