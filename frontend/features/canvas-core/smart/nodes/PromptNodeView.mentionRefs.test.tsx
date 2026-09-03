/**
 * PromptNodeView — a mentioned asset on the input-reference strip.
 *
 * A mention is a REFERENCE, so it has to spend the same budget every other
 * reference spends. Two ways to get that wrong, both silent:
 *
 *   - leave mentions out of the strip's count, and "3 inputs" describes a
 *     request that actually carries five;
 *   - leave them out of the ceiling, and the strip shows nothing dimmed while
 *     the provider quietly drops the tail (the P4 "选了也生成了但图里没有"
 *     failure, one layer up).
 *
 * `max_refs` is the PROVIDER's ceiling and the trim is from the tail, so the
 * greyed entries are exactly the ones past position `max_refs`.
 */

import { ReactFlowProvider } from '@xyflow/react';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

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
vi.mock('../../../../hooks/useResourceSearch', () => ({
  useResourceSearch: () => ({
    data: {
      results: [],
      counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
      next_cursor: null,
    },
    loading: false,
    error: null,
  }),
}));
vi.mock('../../../../services/assetsService', () => ({
  searchAssets: vi.fn().mockResolvedValue([]),
  fetchBundle: vi.fn(),
  fetchAssetDetail: vi.fn(),
}));
vi.mock('./useGenerationModels', () => ({ useGenerationModels: () => [] }));
// The provider ceiling. `null` means UNKNOWN and must grey nothing, which is
// its own case below.
const maxRefs = vi.fn<() => number | null>(() => 1);
vi.mock('./useModelCapabilities', () => ({
  // The WHOLE capability row, not just the field under test: the footer reads
  // `ratios` from the same object, and a half-shaped fixture crashes it in a
  // way that says nothing about references.
  useModelCapabilities: () => {
    const v = maxRefs();
    return v === null
      ? null
      : {
          ratios: ['1:1', '16:9'],
          quality: false,
          resolution: false,
          max_refs: v,
          negative: false,
          video_modes: [],
        };
  },
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { CanvasNode } from '../../types';
import { PromptNodeView } from './PromptNodeView';

const BASE_PROPS = {
  type: 'prompt',
  selected: false,
  dragging: false,
  zIndex: 0,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  deletable: true,
  draggable: true,
  selectable: true,
} as const;

const URL_A = '/api/v1/generated-media/a.png';

const mention = (id: string, name: string) => ({
  asset_id: id,
  name,
  asset_type: 'character' as const,
  cover_file_id: null,
});

function seed(opts: { withInput: boolean; mentions: ReturnType<typeof mention>[] }) {
  useCanvasCoreStore.getState().reset();
  const media: CanvasNode = {
    id: 'm1',
    type: 'media',
    position: { x: 0, y: 0 },
    data: { title: 'Media', items: [{ url: URL_A, kind: 'image', name: 'a.png' }] },
  } as unknown as CanvasNode;
  const prompt: CanvasNode = {
    id: 'p1',
    type: 'prompt',
    position: { x: 0, y: 300 },
    data: {
      body: '',
      provider_slug: '',
      agent_id: null,
      run_status: 'idle',
      resource_refs: [],
      mentioned_assets: opts.mentions,
      gen: { kind: 'image', model: 'ark-lite', ratio: '1:1', count: 1 },
    },
  } as unknown as CanvasNode;
  useCanvasCoreStore.setState({
    kind: 'smart',
    nodes: opts.withInput ? [media, prompt] : [prompt],
    connections: opts.withInput
      ? [{ id: 'e1', source: 'm1', target: 'p1', sourceHandle: null, targetHandle: null }]
      : [],
    selection: [],
  } as never);
  const data = (useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as { id: string }).id === 'p1') as { data: unknown }).data;
  render(
    <ReactFlowProvider>
      <PromptNodeView {...BASE_PROPS} id="p1" type="prompt" data={data as never} />
    </ReactFlowProvider>,
  );
}

afterEach(() => {
  cleanup();
  maxRefs.mockReturnValue(1);
  useCanvasCoreStore.getState().reset();
});

describe('mentions on the reference strip', () => {
  it('a mention alone still draws the strip and counts as an input', () => {
    maxRefs.mockReturnValue(null);
    seed({ withInput: false, mentions: [mention('1', 'Ava')] });
    const row = screen.getByTestId('prompt-input-row');
    expect(row.textContent).toContain('1 inputs');
    expect(screen.getAllByTestId('prompt-mention-thumb')).toHaveLength(1);
  });

  it('mentions are numbered AFTER the wired inputs — delivery order', () => {
    maxRefs.mockReturnValue(null);
    seed({ withInput: true, mentions: [mention('1', 'Ava'), mention('2', 'Alley')] });
    const row = screen.getByTestId('prompt-input-row');
    expect(row.textContent).toContain('3 inputs');
    const thumbs = screen.getAllByTestId('prompt-mention-thumb');
    expect(thumbs[0].textContent).toBe('2');
    expect(thumbs[1].textContent).toBe('3');
  });

  it('greys the mentions past the provider ceiling, and only those', () => {
    // Two slots: the wired input takes the first, the first mention takes the
    // second, and the second mention is past the ceiling.
    maxRefs.mockReturnValue(2);
    seed({ withInput: true, mentions: [mention('1', 'Ava'), mention('2', 'Alley')] });
    const thumbs = screen.getAllByTestId('prompt-mention-thumb');
    expect(thumbs[0].getAttribute('data-beyond-limit')).toBe('false');
    expect(thumbs[1].getAttribute('data-beyond-limit')).toBe('true');
  });

  it('an unknown ceiling greys NOTHING — null is not zero', () => {
    maxRefs.mockReturnValue(null);
    seed({ withInput: true, mentions: [mention('1', 'Ava'), mention('2', 'Alley')] });
    for (const thumb of screen.getAllByTestId('prompt-mention-thumb')) {
      expect(thumb.getAttribute('data-beyond-limit')).toBe('false');
    }
  });

  it('no inputs and no mentions means no strip at all', () => {
    maxRefs.mockReturnValue(null);
    seed({ withInput: false, mentions: [] });
    expect(screen.queryByTestId('prompt-input-row')).toBeNull();
  });
});
