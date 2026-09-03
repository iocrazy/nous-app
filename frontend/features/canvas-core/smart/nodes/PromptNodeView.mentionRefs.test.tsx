/**
 * PromptNodeView — the input-reference strip, drawn in DELIVERY order.
 *
 * `generationRunner` builds `[...asset references, ...wired images]`, so the
 * strip has to draw mentions FIRST. It used to draw them last and number them
 * after the wired inputs, which meant that with `max_refs = 2`, one wired
 * input and two mentions it dimmed the second mention while the backend was
 * dropping the wired input — a badge that describes the run confidently and
 * wrongly, which this repo has paid for before.
 *
 * The arithmetic itself lives in `promptStrip.ts` and is pinned there. What
 * this file covers is the wiring: that the component draws what that function
 * returns, in that order, and says nothing where it declines to answer.
 */

import { ReactFlowProvider } from '@xyflow/react';
import { cleanup, render, screen, within } from '@testing-library/react';
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
// The provider ceiling. `null` means UNKNOWN and must dim nothing, which is
// its own case below.
const maxRefs = vi.fn<() => number | null>(() => null);
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
import { assetMentionToken } from '../mentionedAssets';

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

const URL_A = '/api/v1/generated-media/a/cover';
const URL_B = '/api/v1/generated-media/b/cover';

/** `ref_resource_ids` omitted = NOT ASKED, which is a different state from []. */
const mention = (id: string, refs?: string[]) => ({
  asset_id: id,
  name: `Asset ${id}`,
  asset_type: 'character' as const,
  cover_file_id: null,
  ...(refs ? { ref_resource_ids: refs } : {}),
});

interface Seed {
  inputs?: string[];
  mentions?: ReturnType<typeof mention>[];
  /** A connected asset card's checklist — references AHEAD of this strip. */
  cardFiles?: string[];
}

function seed({ inputs = [], mentions = [], cardFiles }: Seed) {
  useCanvasCoreStore.getState().reset();
  const nodes: CanvasNode[] = [];
  const connections: unknown[] = [];
  if (inputs.length > 0) {
    nodes.push({
      id: 'm1',
      type: 'media',
      position: { x: 0, y: 0 },
      data: { title: 'Media', items: inputs.map((url) => ({ url, kind: 'image' })) },
    } as unknown as CanvasNode);
    connections.push({ id: 'e-m', source: 'm1', target: 'p1', sourceHandle: null, targetHandle: null });
  }
  if (cardFiles) {
    nodes.push({
      id: 'a1',
      type: 'asset',
      position: { x: 0, y: 0 },
      data: {
        asset_id: '900',
        loadout_id: null,
        selected_file_ids: cardFiles,
        name: 'Wired',
        asset_type: 'character',
        cover_file_id: null,
        readiness_state: 'ready',
      },
    } as unknown as CanvasNode);
    connections.push({ id: 'e-a', source: 'a1', target: 'p1', sourceHandle: null, targetHandle: null });
  }
  nodes.push({
    id: 'p1',
    type: 'prompt',
    position: { x: 0, y: 300 },
    data: {
      // The token has to be IN the body. `mentioned_assets` is derived from the
      // document on every change, so a record with no token in the text is
      // pruned on mount — correctly, since the chip is the reference. A fixture
      // that omitted it would be testing a state the app never persists.
      body: mentions.map((m) => assetMentionToken(m.asset_id)).join(' '),
      provider_slug: '',
      agent_id: null,
      run_status: 'idle',
      resource_refs: [],
      mentioned_assets: mentions,
      gen: { kind: 'image', model: 'ark-lite', ratio: '1:1', count: 1 },
    },
  } as unknown as CanvasNode);
  useCanvasCoreStore.setState({ kind: 'smart', nodes, connections, selection: [] } as never);
  const data = (useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as { id: string }).id === 'p1') as { data: unknown }).data;
  render(
    <ReactFlowProvider>
      <PromptNodeView {...BASE_PROPS} id="p1" type="prompt" data={data as never} />
    </ReactFlowProvider>,
  );
}

/** Every tile on the strip, in DOM order, as `kind:identifier`. */
function tiles(): string[] {
  const row = screen.getByTestId('prompt-input-row');
  return [...row.querySelectorAll('[data-testid="prompt-mention-thumb"],[data-testid="prompt-input-thumb"]')]
    .map((el) =>
      el.getAttribute('data-testid') === 'prompt-mention-thumb'
        ? `mention:${el.getAttribute('data-asset-id')}`
        : 'input',
    );
}

function positions(): (string | null)[] {
  const row = screen.getByTestId('prompt-input-row');
  return [...row.querySelectorAll('[data-position]')].map((el) => el.getAttribute('data-position'));
}

afterEach(() => {
  cleanup();
  maxRefs.mockReturnValue(null);
  useCanvasCoreStore.getState().reset();
});

describe('the strip is drawn in delivery order', () => {
  it('mentions come BEFORE the wired images', () => {
    // The mutation this exists for: swap the two groups in the component and
    // this goes red while every arithmetic test still passes.
    seed({ inputs: [URL_A], mentions: [mention('7', ['70'])] });
    expect(tiles()).toEqual(['mention:7', 'input']);
  });

  it('numbers them in that order too', () => {
    seed({ inputs: [URL_A], mentions: [mention('7', ['70', '71'])] });
    // One mention, TWO references behind one thumbnail — the wired image is
    // the third reference, not the second.
    expect(positions()).toEqual(['1', '3']);
  });

  it('counts a connected asset card ahead of the whole strip', () => {
    seed({ inputs: [URL_A], mentions: [mention('7', ['70'])], cardFiles: ['10', '11'] });
    expect(positions()).toEqual(['3', '4']);
  });
});

describe('the strip and the provider ceiling', () => {
  it('dims the mention when the cards ahead already filled the ceiling', () => {
    maxRefs.mockReturnValue(2);
    seed({ mentions: [mention('7', ['70'])], cardFiles: ['10', '11'] });
    expect(
      screen.getByTestId('prompt-mention-thumb').getAttribute('data-beyond-limit'),
    ).toBe('true');
  });

  it('dims the wired image the tail trim actually drops', () => {
    maxRefs.mockReturnValue(2);
    seed({ inputs: [URL_A, URL_B], mentions: [mention('7', ['70'])] });
    const dimmed = [
      ...screen.getByTestId('prompt-input-row').querySelectorAll('[data-beyond-limit]'),
    ].map((el) => el.getAttribute('data-beyond-limit'));
    expect(dimmed).toEqual(['false', 'false', 'true']);
  });

  it('an unknown ceiling dims nothing', () => {
    maxRefs.mockReturnValue(null);
    seed({ inputs: [URL_A, URL_B], mentions: [mention('7', ['70'])] });
    for (const el of screen
      .getByTestId('prompt-input-row')
      .querySelectorAll('[data-beyond-limit]')) {
      expect(el.getAttribute('data-beyond-limit')).toBe('false');
    }
  });
});

describe('the strip declines to number what it cannot know', () => {
  it('a mention with no snapshot carries no badge, and neither does anything after it', () => {
    maxRefs.mockReturnValue(1);
    seed({ inputs: [URL_A], mentions: [mention('7')] });
    // `data-position` is emitted empty rather than as a made-up number.
    expect(positions()).toEqual(['', '']);
    const row = screen.getByTestId('prompt-input-row');
    for (const el of row.querySelectorAll('[data-beyond-limit]')) {
      expect(el.getAttribute('data-beyond-limit')).toBe('false');
    }
  });
});

describe('the strip as a whole', () => {
  it('a mention alone still draws the strip and counts as an input', () => {
    seed({ mentions: [mention('7', ['70'])] });
    const row = screen.getByTestId('prompt-input-row');
    expect(within(row).getAllByTestId('prompt-mention-thumb')).toHaveLength(1);
    expect(row.textContent).toContain('1 inputs');
  });

  it('counts tiles, not references — the count names what is on screen', () => {
    seed({ inputs: [URL_A], mentions: [mention('7', ['70', '71'])] });
    expect(screen.getByTestId('prompt-input-row').textContent).toContain('2 inputs');
  });

  it('no inputs and no mentions means no strip at all', () => {
    seed({});
    expect(screen.queryByTestId('prompt-input-row')).toBeNull();
  });
});
