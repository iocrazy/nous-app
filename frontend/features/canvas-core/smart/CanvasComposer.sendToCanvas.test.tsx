// features/canvas-core/smart/CanvasComposer.sendToCanvas.test.tsx
// Phase 2 Task 4 (spec 2026-07-26-asset-prompt-management): CanvasComposer
// consumes `location.state.promptInsert` — the payload SendToCanvasModal
// navigates here with — once the canvas has finished loading, turning it
// into a Prompt node + Media node pair, then clears the router state so a
// reload/back-nav doesn't reinsert it.
//
// The node/connection SHAPE (lang fallback, cover url, position offset,
// connection id) is already covered by loadPromptAsset.test.ts — the pure
// builder this wiring reuses. This file only proves the composer-level
// wiring fires at the right time and clears up after itself; it does NOT
// re-derive the full canvas surface (surfaceRef geometry, viewport, React
// Flow) that a true end-to-end drop-position test would need.

import { StrictMode } from 'react';
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const navigate = vi.fn();
let locationState: unknown = null;
let locationSearch = '';
let locationHash = '';
vi.mock('react-router-dom', () => ({
  useLocation: () => ({
    pathname: '/team/t1/canvas/c1',
    search: locationSearch,
    hash: locationHash,
    state: locationState,
  }),
  useNavigate: () => navigate,
}));

const mockImportResource = vi.fn();
vi.mock('./mediaImport', () => ({
  importResourceAsCanvasMedia: (...args: unknown[]) => mockImportResource(...args),
}));
// ⚡ Generate Similar (spec 2026-07-28-prompt-dataline, Task 5): the
// autoRun path calls rerunPrompt(id) after the node lands — mocked so
// these tests stay store-only, same as the rest of this file.
const mockRerunPrompt = vi.fn();
vi.mock('./regenerate', () => ({
  rerunPrompt: (...args: unknown[]) => mockRerunPrompt(...args),
}));
// CanvasComposer only pulls the PromptAsset type + getResourceCoverUrl (the
// mint-failure fallback) from resourceService — full mock avoids pulling in
// supabaseClient's env-var-dependent init through the real module.
vi.mock('../../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
}));

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { CanvasComposer } from './CanvasComposer';
import { _resetIdCounter } from './factories';

beforeEach(() => {
  navigate.mockReset();
  useCanvasCoreStore.getState().reset();
  mockImportResource.mockReset();
  mockImportResource.mockResolvedValue({ url: '/api/v1/generated-media/gm-1', kind: 'image' });
  mockRerunPrompt.mockReset().mockResolvedValue(true);
});

afterEach(() => {
  useCanvasCoreStore.getState().reset();
  _resetIdCounter();
  locationState = null;
  locationSearch = '';
  locationHash = '';
});

describe('CanvasComposer — Send to Canvas consumption', () => {
  it('turns a pending promptInsert into a Prompt + Media node pair once ready, then clears router state', async () => {
    locationState = {
      promptInsert: {
        assetId: 'r1',
        filename: 'hero.png',
        positive: 'a cinematic hero shot',
        negative: 'lowres',
      },
    };
    useCanvasCoreStore.setState({ canvasId: 'c1', kind: 'smart', loadStatus: 'ready' });

    render(<CanvasComposer />);

    await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(2));
    const { nodes, connections, selection } = useCanvasCoreStore.getState();
    const promptNode = nodes.find(
      (n) => (n as Record<string, unknown>).type === 'prompt',
    ) as Record<string, unknown>;
    const mediaNode = nodes.find(
      (n) => (n as Record<string, unknown>).type === 'media',
    ) as Record<string, unknown>;
    expect(promptNode).toBeTruthy();
    expect(mediaNode).toBeTruthy();
    expect((promptNode.data as Record<string, unknown>).body).toBe('a cinematic hero shot');
    expect((promptNode.data as Record<string, unknown>).negative_body).toBe('lowres');
    expect(connections).toHaveLength(1);
    expect(connections[0]).toMatchObject({ source: mediaNode.id, target: promptNode.id });
    expect(selection).toEqual([promptNode.id, mediaNode.id]);

    expect(navigate).toHaveBeenCalledWith('/team/t1/canvas/c1', { replace: true });
  });

  it('preserves search/hash when clearing router state after consuming a promptInsert (M4)', async () => {
    locationState = {
      promptInsert: {
        assetId: 'r1',
        filename: 'hero.png',
        positive: 'a cinematic hero shot',
        negative: 'lowres',
      },
    };
    locationSearch = '?foo=bar';
    locationHash = '#section';
    useCanvasCoreStore.setState({ canvasId: 'c1', kind: 'smart', loadStatus: 'ready' });

    render(<CanvasComposer />);

    await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(2));
    expect(navigate).toHaveBeenCalledWith('/team/t1/canvas/c1?foo=bar#section', { replace: true });
  });

  it('inserts exactly once under React.StrictMode double-invoke (insertedRef guard)', async () => {
    locationState = {
      promptInsert: {
        assetId: 'r1',
        filename: 'hero.png',
        positive: 'a cinematic hero shot',
        negative: 'lowres',
      },
    };
    useCanvasCoreStore.setState({ canvasId: 'c1', kind: 'smart', loadStatus: 'ready' });

    render(
      <StrictMode>
        <CanvasComposer />
      </StrictMode>,
    );

    await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(2));
    // Give any StrictMode re-run a chance to (wrongly) insert a second pair
    // before asserting the final counts stay put.
    await new Promise((resolve) => setTimeout(resolve, 0));

    const { nodes, connections } = useCanvasCoreStore.getState();
    expect(nodes).toHaveLength(2);
    expect(connections).toHaveLength(1);
    expect(navigate).toHaveBeenCalledTimes(1);
  });

  it('does nothing when there is no pending promptInsert', () => {
    locationState = null;
    useCanvasCoreStore.setState({ canvasId: 'c1', kind: 'smart', loadStatus: 'ready' });
    render(<CanvasComposer />);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(0);
    expect(navigate).not.toHaveBeenCalled();
  });

  it('waits for loadStatus to be ready before consuming', () => {
    locationState = {
      promptInsert: { assetId: 'r1', filename: 'hero.png', positive: 'p' },
    };
    useCanvasCoreStore.setState({ canvasId: 'c1', kind: 'smart', loadStatus: 'loading' });
    render(<CanvasComposer />);
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(0);
    expect(navigate).not.toHaveBeenCalled();
  });

  // ─── ⚡ Generate Similar (spec 2026-07-28-prompt-dataline, Task 5) ────

  describe('autoRun payload', () => {
    it('merges gen settings into the inserted prompt node and reruns it exactly once', async () => {
      locationState = {
        promptInsert: {
          assetId: 'r1',
          filename: 'hero.png',
          positive: 'a cinematic hero shot',
          autoRun: true,
          ratio: '16:9',
        },
      };
      useCanvasCoreStore.setState({ canvasId: 'c1', kind: 'smart', loadStatus: 'ready' });

      render(<CanvasComposer />);

      await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(2));
      const promptNode = useCanvasCoreStore
        .getState()
        .nodes.find((n) => (n as Record<string, unknown>).type === 'prompt') as Record<string, unknown>;
      const data = promptNode.data as Record<string, unknown>;
      expect(data.gen).toEqual({ kind: 'image', model: '', ratio: '16:9' });

      expect(mockRerunPrompt).toHaveBeenCalledTimes(1);
      expect(mockRerunPrompt).toHaveBeenCalledWith(promptNode.id);
    });

    it('inserts exactly once and reruns exactly once under React.StrictMode double-invoke', async () => {
      locationState = {
        promptInsert: {
          assetId: 'r1',
          filename: 'hero.png',
          positive: 'a cinematic hero shot',
          autoRun: true,
          ratio: '3:4',
        },
      };
      useCanvasCoreStore.setState({ canvasId: 'c1', kind: 'smart', loadStatus: 'ready' });

      render(
        <StrictMode>
          <CanvasComposer />
        </StrictMode>,
      );

      await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(2));
      // Give any StrictMode re-run a chance to (wrongly) fire a second rerun
      // before asserting the final count stays put.
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(useCanvasCoreStore.getState().nodes).toHaveLength(2);
      expect(mockRerunPrompt).toHaveBeenCalledTimes(1);
    });

    it('omits an invalid ratio instead of passing it through to gen (M4)', async () => {
      locationState = {
        promptInsert: {
          assetId: 'r1',
          filename: 'hero.png',
          positive: 'a cinematic hero shot',
          autoRun: true,
          ratio: 'not-a-ratio',
        },
      };
      useCanvasCoreStore.setState({ canvasId: 'c1', kind: 'smart', loadStatus: 'ready' });

      render(<CanvasComposer />);

      await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(2));
      const promptNode = useCanvasCoreStore
        .getState()
        .nodes.find((n) => (n as Record<string, unknown>).type === 'prompt') as Record<string, unknown>;
      const data = promptNode.data as Record<string, unknown>;
      expect(data.gen).toEqual({ kind: 'image', model: '', ratio: undefined });
    });

    it('omits a missing ratio the same way (M4)', async () => {
      locationState = {
        promptInsert: {
          assetId: 'r1',
          filename: 'hero.png',
          positive: 'a cinematic hero shot',
          autoRun: true,
        },
      };
      useCanvasCoreStore.setState({ canvasId: 'c1', kind: 'smart', loadStatus: 'ready' });

      render(<CanvasComposer />);

      await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(2));
      const promptNode = useCanvasCoreStore
        .getState()
        .nodes.find((n) => (n as Record<string, unknown>).type === 'prompt') as Record<string, unknown>;
      const data = promptNode.data as Record<string, unknown>;
      expect(data.gen).toEqual({ kind: 'image', model: '', ratio: undefined });
    });

    it('does not merge gen or call rerunPrompt when autoRun is absent (non-autoRun flow unchanged)', async () => {
      locationState = {
        promptInsert: {
          assetId: 'r1',
          filename: 'hero.png',
          positive: 'a cinematic hero shot',
        },
      };
      useCanvasCoreStore.setState({ canvasId: 'c1', kind: 'smart', loadStatus: 'ready' });

      render(<CanvasComposer />);

      await waitFor(() => expect(useCanvasCoreStore.getState().nodes).toHaveLength(2));
      const promptNode = useCanvasCoreStore
        .getState()
        .nodes.find((n) => (n as Record<string, unknown>).type === 'prompt') as Record<string, unknown>;
      const data = promptNode.data as Record<string, unknown>;
      expect(data.gen).toBeUndefined();
      expect(mockRerunPrompt).not.toHaveBeenCalled();
    });
  });
});
