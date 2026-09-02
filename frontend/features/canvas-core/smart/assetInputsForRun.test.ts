// features/canvas-core/smart/assetInputsForRun.test.ts
//
// The seam between the pure composition and the two ambient things a run needs:
// the live graph, and the team scope in the URL. Sibling of the `droppedKnobs`
// seam tests — the property under test is that the report lands on the CARD,
// every run, including the clean one.

import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchBundle = vi.fn();
vi.mock('../../../services/assetsService', () => ({
  fetchBundle: (...a: unknown[]) => fetchBundle(...a),
}));

import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { resolveAssetInputsForRun } from './assetInputs';
import { currentCanvasScopeId, scopeIdFromPathname } from './canvasScope';
import type { AssetNodeData } from './types';

const SCOPE = '727145299382534100';

const assetNode = (id: string, over: Partial<AssetNodeData> = {}) => ({
  id,
  type: 'asset',
  position: { x: 0, y: 0 },
  data: {
    asset_id: '55',
    loadout_id: null,
    selected_file_ids: ['10'],
    name: 'Ava',
    asset_type: 'character',
    cover_file_id: null,
    readiness_state: 'ready',
    ...over,
  },
});

const cardData = (id: string): AssetNodeData =>
  (useCanvasCoreStore.getState().nodes.find((n) => (n as { id: string }).id === id) as {
    data: AssetNodeData;
  }).data;

function seed() {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    loadStatus: 'ready',
    nodes: [
      assetNode('a1'),
      { id: 'p1', type: 'prompt', position: { x: 0, y: 0 }, data: { body: 'x' } },
    ],
    connections: [{ id: 'e1', source: 'a1', target: 'p1' }],
  } as never);
}

beforeEach(() => {
  fetchBundle.mockReset();
  vi.spyOn(console, 'error').mockImplementation(() => {});
  window.history.pushState({}, '', `/team/${SCOPE}/canvas/9`);
  seed();
});

describe('scope, read from the URL', () => {
  it('reads the team segment of a canvas path', () => {
    expect(currentCanvasScopeId()).toBe(SCOPE);
  });

  it('answers empty for a path with no team segment', () => {
    expect(scopeIdFromPathname('/canvas/9')).toBe('');
    expect(scopeIdFromPathname('/')).toBe('');
    // Not a team segment: the router matches `/team/:teamId` at the ROOT.
    expect(scopeIdFromPathname('/x/team/5/canvas/9')).toBe('');
  });

  it('stops at the next segment, the query and the hash', () => {
    expect(scopeIdFromPathname('/team/5')).toBe('5');
    expect(scopeIdFromPathname('/team/5/canvas/9')).toBe('5');
    expect(scopeIdFromPathname('/team/5?tab=x')).toBe('5');
  });
});

describe('resolveAssetInputsForRun', () => {
  it('asks the bundle endpoint with the URL scope and returns the composition', async () => {
    fetchBundle.mockResolvedValue({
      prompt: { positive: 'Ava, red coat', negative: '' },
      reference_resource_ids: ['10'],
      dropped: [],
      max_refs: 9,
    });
    const out = await resolveAssetInputsForRun('p1', 'codex');
    expect(fetchBundle).toHaveBeenCalledWith(SCOPE, '55', {
      model: 'codex',
      loadoutId: undefined,
      // The card's checklist rides along so the provider ceiling is applied
      // WITHIN it, server-side. Losing it here is the C1 defect.
      selectedFileIds: ['10'],
    });
    expect(out.reference_urls).toEqual(['/api/v1/resources/10/cover']);
    expect(out.prompt_prefix).toBe('Ava, red coat');
  });

  it('writes what the bundle would not send onto the CARD', async () => {
    fetchBundle.mockResolvedValue({
      prompt: { positive: '', negative: '' },
      reference_resource_ids: [],
      dropped: [{ resource_id: '11', reason: 'over_limit' }],
      max_refs: 1,
    });
    await resolveAssetInputsForRun('p1', 'codex');
    expect(cardData('a1').last_bundle_dropped).toEqual([
      { resource_id: '11', reason: 'over_limit' },
    ]);
    expect(cardData('a1').last_bundle_error).toBeNull();
  });

  it('CLEARS a previous run’s verdict on a clean run', async () => {
    fetchBundle.mockResolvedValueOnce({
      prompt: { positive: '', negative: '' },
      reference_resource_ids: [],
      dropped: [{ resource_id: '11', reason: 'over_limit' }],
      max_refs: 1,
    });
    await resolveAssetInputsForRun('p1', 'codex');
    expect(cardData('a1').last_bundle_dropped).toHaveLength(1);

    fetchBundle.mockResolvedValueOnce({
      prompt: { positive: '', negative: '' },
      reference_resource_ids: ['10'],
      dropped: [],
      max_refs: 9,
    });
    await resolveAssetInputsForRun('p1', 'codex');
    expect(cardData('a1').last_bundle_dropped).toEqual([]);
  });

  it('records a failed bundle on the card rather than swallowing it', async () => {
    fetchBundle.mockRejectedValue(new Error('HTTP 500'));
    const out = await resolveAssetInputsForRun('p1', 'codex');
    expect(out.reference_urls).toEqual([]);
    expect(cardData('a1').last_bundle_error).toBe('HTTP 500');
  });

  it('records a missing scope on the card', async () => {
    window.history.pushState({}, '', '/canvas/9');
    await resolveAssetInputsForRun('p1', 'codex');
    expect(fetchBundle).not.toHaveBeenCalled();
    expect(cardData('a1').last_bundle_error).toBe('no_scope');
  });

  it('reads the LIVE graph, not a snapshot', async () => {
    // A chain run resolves each prompt just before dispatch precisely because
    // earlier prompts in the same chain change the graph while it runs.
    fetchBundle.mockResolvedValue({
      prompt: { positive: '', negative: '' },
      reference_resource_ids: [],
      dropped: [],
      max_refs: 9,
    });
    useCanvasCoreStore.setState({ connections: [] } as never);
    await resolveAssetInputsForRun('p1', 'codex');
    expect(fetchBundle).not.toHaveBeenCalled();
  });
});
