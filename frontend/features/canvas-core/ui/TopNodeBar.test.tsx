/**
 * TopNodeBar — Standard canvas top node strip (Phase 2.2). Pins: a chip
 * click drops the node at the viewport centre through the store; the
 * Image/Video Gen chips create prompts pre-set to that generation kind.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TopNodeBar } from './TopNodeBar';
import { useCanvasCoreStore } from '../store/canvasCoreStore';

// The Asset chip opens the library picker instead of creating outright, so
// the two assets calls it makes are stubbed. Ids are strings — that is what
// the assets router puts on the wire for every BIGINT column.
const SCOPE = '727145299382534100';
const ASSET_ID = '727145299382534300';
const SUMMARY = {
  id: ASSET_ID,
  scope_id: SCOPE,
  asset_type: 'character' as const,
  name: 'Cole Bannon',
  role_tag: 'lead',
  readiness: { state: 'ready' as const, missing: [] },
  cover_file_id: null,
  is_system_preset: false,
};
const searchAssets = vi.fn();
const fetchAssetDetail = vi.fn();

vi.mock('../../../services/assetsService', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    searchAssets: (...a: unknown[]) => searchAssets(...a),
    fetchAssetDetail: (...a: unknown[]) => fetchAssetDetail(...a),
  };
});

const surfaceRef = {
  current: {
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
  } as unknown as HTMLDivElement,
};

beforeEach(() => {
  searchAssets.mockReset().mockResolvedValue([SUMMARY]);
  fetchAssetDetail
    .mockReset()
    .mockResolvedValue({
      ...SUMMARY,
      files: [],
      links: [],
      linked_by: [],
      loadouts: [],
      used_in: { canvases: [], storyboards: [] },
    });
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: 'c1',
    kind: 'smart',
    loadStatus: 'ready',
    viewport: { x: 0, y: 0, zoom: 1 },
  } as never);
});

afterEach(() => {
  cleanup();
  useCanvasCoreStore.getState().reset();
});

function renderInRoute() {
  return render(
    <MemoryRouter initialEntries={[`/team/${SCOPE}/canvas/9`]}>
      <Routes>
        <Route
          path="/team/:teamId/canvas/:canvasId"
          element={<TopNodeBar surfaceRef={surfaceRef} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('TopNodeBar', () => {
  it('renders one chip per node type', () => {
    render(<TopNodeBar surfaceRef={surfaceRef} />);
    for (const label of [
      'Upload',
      'Shot',
      'Prompt',
      'LLM',
      'Image Gen',
      'Video Gen',
      'Loop',
      'Timeline',
      'Output',
      'Asset',
    ]) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy();
    }
  });

  it('a chip click appends the node at the viewport centre and selects it', () => {
    render(<TopNodeBar surfaceRef={surfaceRef} />);
    fireEvent.click(screen.getByRole('button', { name: 'LLM' }));
    const state = useCanvasCoreStore.getState();
    expect(state.nodes).toHaveLength(1);
    const node = state.nodes[0] as Record<string, unknown>;
    expect(node.type).toBe('llm');
    expect(node.position).toEqual({ x: 400, y: 300 });
    expect(state.selection).toEqual([node.id]);
  });

  it('Image Gen / Video Gen create prompts pre-set to that kind', () => {
    render(<TopNodeBar surfaceRef={surfaceRef} />);
    fireEvent.click(screen.getByRole('button', { name: 'Image Gen' }));
    fireEvent.click(screen.getByRole('button', { name: 'Video Gen' }));
    const [img, vid] = useCanvasCoreStore.getState().nodes as Array<{
      type: string;
      data: { gen?: { kind: string } };
    }>;
    expect(img.type).toBe('prompt');
    expect(img.data.gen?.kind).toBe('image');
    expect(vid.data.gen?.kind).toBe('video');
  });

  it('the Asset chip creates nothing on its own — it opens the picker', () => {
    renderInRoute();
    fireEvent.click(screen.getByRole('button', { name: 'Asset' }));
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(0);
    expect(screen.getByRole('dialog', { name: 'Add Asset' })).toBeTruthy();
  });

  it('picking an asset drops a bound card at the viewport centre', async () => {
    renderInRoute();
    fireEvent.click(screen.getByRole('button', { name: 'Asset' }));
    fireEvent.click(await screen.findByTestId('asset-picker-row'));
    await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(1));
    const node = useCanvasCoreStore.getState().nodes[0] as {
      type: string;
      position: { x: number; y: number };
      data: { asset_id: string; loadout_id: string | null };
    };
    expect(node.type).toBe('asset');
    expect(node.data.asset_id).toBe(ASSET_ID);
    expect(node.data.loadout_id).toBeNull();
    expect(node.position).toEqual({ x: 400, y: 300 });
    expect(useCanvasCoreStore.getState().selection).toHaveLength(1);
  });
});
