/**
 * PromptNodeView — the `@` picker.
 *
 * Rewritten when the picker stopped being a whole-library resource list and
 * became two thumbnail grids (Input Images / Assets). The flow under test is
 * end to end:
 *
 *   type @ → picker opens → pick an asset → an inline chip lands in the body,
 *   the body text carries the stable `@[asset:id]` token, and node data
 *   records the mention. Delete the chip and the record goes with it.
 *
 * The body is a tiptap contenteditable, so `fireEvent.change` cannot drive it
 * — ProseMirror syncs from the DOM. `typeBody` writes the text and fires
 * `input`, which is what a keystroke ends up doing; that sync lands a
 * microtask later, so this suite runs on real timers and awaits.
 *
 * ⚠️ The asset rows are WIRE shapes: `id` / `scope_id` / `cover_file_id` are
 * STRINGS, because the assets repository stringifies BIGINT. A fixture that
 * "tidied" them into numbers would exercise a branch production never takes
 * (CLAUDE.md — 边界 mock 必须用真实 JSON 形状).
 */

import { ReactFlowProvider } from '@xyflow/react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';

// ── Module mocks ──────────────────────────────────────────────────────────────

// i18n: passthrough that honours the English default every call passes.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      typeof d === 'string' ? d : typeof d === 'object' && d !== null && 'defaultValue' in d
        ? String((d as { defaultValue: unknown }).defaultValue)
        : k,
  }),
}));

// The "Add reference image" picker still asks the resource library; the `@`
// picker no longer does. Stubbed so nothing here touches the network.
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

const searchAssets = vi.fn();
// Picking a mention fetches the asset's DETAIL, to snapshot its primary-slot
// files onto the record — that is what lets the reference strip say how many
// references one mention stands for instead of guessing.
const fetchAssetDetail = vi.fn();
vi.mock('../../../../services/assetsService', () => ({
  searchAssets: (...a: unknown[]) => searchAssets(...a),
  fetchAssetDetail: (...a: unknown[]) => fetchAssetDetail(...a),
  // Present because the run-path composition imports it from this module.
  // A mock that omits a named import throws at module load.
  fetchBundle: vi.fn(),
}));

// The canvas takes its asset scope from the route. These tests render the node
// without a Router, where `useParams` answers `{}` — which would make every
// case exercise the "no scope" branch. Mocked to a real Snowflake string.
const SCOPE = '727145299382534100';
vi.mock('../canvasScope', () => ({
  useCanvasScope: () => ({ scopeId: SCOPE, resPath: (p: string) => p }),
  currentCanvasScopeId: () => SCOPE,
  scopeIdFromPathname: () => SCOPE,
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { PromptNodeView } from './PromptNodeView';
import type { AssetSummary } from '../../../../services/assetsService';

// ── Fixtures ──────────────────────────────────────────────────────────────────

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

const ALLEY: AssetSummary = {
  id: '727145299382534202',
  scope_id: SCOPE,
  asset_type: 'location',
  name: 'Back Alley',
  role_tag: '',
  readiness: { state: 'draft', missing: ['establishing'] },
  cover_file_id: null,
  is_system_preset: false,
};

/** `GET /assets/{id}` — string ids, `asset_files` rows with slot/sort_order.
 *  The primary slot for `character` is `sheet`; `worn` belongs to an outfit and
 *  a mention binds none. */
const AVA_DETAIL = {
  ...AVA,
  subtype: null,
  description: '',
  attrs: {},
  prompt_positive: null,
  prompt_negative: null,
  prompt_positive_zh: null,
  prompt_negative_zh: null,
  platform_params: {},
  source: 'manual',
  duplicated_from: null,
  in_library: true,
  files: [
    {
      asset_id: AVA.id,
      resource_id: '727145299382534401',
      slot: 'sheet',
      loadout_id: null,
      sort_order: 0,
      note: null,
      attached_by: null,
      attached_at: '2026-09-01T00:00:00Z',
    },
    {
      asset_id: AVA.id,
      resource_id: '727145299382534402',
      slot: 'worn',
      loadout_id: '727145299382534500',
      sort_order: 0,
      note: null,
      attached_by: null,
      attached_at: '2026-09-01T00:00:00Z',
    },
  ],
  links: [],
  linked_by: [],
  loadouts: [],
};

function Wrap({ children }: { children: ReactNode }) {
  return <ReactFlowProvider>{children}</ReactFlowProvider>;
}

function seedPromptNode(id: string, overrides: Record<string, unknown> = {}) {
  const data = {
    body: '',
    provider_slug: '',
    agent_id: null,
    run_status: 'idle',
    run_started_at: null,
    run_finished_at: null,
    run_error: null,
    resource_refs: [],
    ...overrides,
  };
  useCanvasCoreStore.setState({
    nodes: [{ id, type: 'prompt', data, position: { x: 0, y: 0 } }],
  } as never);
  return data;
}

const BASE_PROPS = {
  type: 'prompt',
  dragHandle: undefined,
  draggable: true,
  selectable: true,
  deletable: true,
  selected: false,
  dragging: false,
  isConnectable: true,
  positionAbsoluteX: 0,
  positionAbsoluteY: 0,
  width: 280,
  height: 160,
  zIndex: 0,
} as const;

function renderPromptNode(id: string, data: Record<string, unknown>) {
  return render(
    <Wrap>
      <PromptNodeView {...BASE_PROPS} id={id} type="prompt" data={data} />
    </Wrap>,
  );
}

/** Type into the tiptap prompt body, then let ProseMirror's DOM sync and the
 *  React update settle so the assertions after it can stay synchronous. */
async function typeBody(text: string): Promise<void> {
  const el = screen.getByLabelText('Prompt body');
  const block = el.querySelector('p');
  if (!block) throw new Error('prompt body has no paragraph to type into');
  await act(async () => {
    block.textContent = text;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    await Promise.resolve();
  });
}

function nodeData(): Record<string, unknown> {
  return (useCanvasCoreStore.getState().nodes[0] as Record<string, Record<string, unknown>>)
    .data;
}

// ── Setup / Teardown ──────────────────────────────────────────────────────────

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    canvasId: 'test-canvas',
    kind: 'smart',
    loadStatus: 'ready',
    baseUpdatedAt: '2026-06-14T00:00:00+00:00',
  } as never);
  searchAssets.mockReset();
  searchAssets.mockResolvedValue([AVA, ALLEY]);
  fetchAssetDetail.mockReset();
  fetchAssetDetail.mockResolvedValue(AVA_DETAIL);
});

afterEach(() => {
  vi.restoreAllMocks();
  useCanvasCoreStore.getState().reset();
});

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('PromptNodeView — @ picker chrome', () => {
  it('picker is hidden on initial render', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);
    expect(screen.queryByTestId('prompt-mention-picker')).toBeNull();
  });

  it('typing @ opens the picker with both tabs', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');

    expect(screen.getByTestId('prompt-mention-picker')).toBeInTheDocument();
    expect(screen.getByTestId('mention-tab-input')).toBeInTheDocument();
    expect(screen.getByTestId('mention-tab-assets')).toBeInTheDocument();
  });

  it('Escape closes the picker without inserting anything', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');
    expect(screen.getByTestId('prompt-mention-picker')).toBeInTheDocument();

    fireEvent.keyDown(screen.getByLabelText('Prompt body'), { key: 'Escape' });
    expect(screen.queryByTestId('prompt-mention-picker')).toBeNull();
    expect(nodeData().mentioned_assets ?? []).toEqual([]);
  });

  it('Escape STAYS dismissed while the same token is being typed', async () => {
    // Without the latch the next keystroke reported a live `@query` and the
    // popover came straight back, so Escape read as a flicker and there was no
    // way to type a literal `@name`.
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@av');
    expect(screen.getByTestId('prompt-mention-picker')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText('Prompt body'), { key: 'Escape' });
    expect(screen.queryByTestId('prompt-mention-picker')).toBeNull();

    await typeBody('@ava');
    expect(screen.queryByTestId('prompt-mention-picker')).toBeNull();
  });

  it('a FRESH @ asks again — the dismissal was about one token', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@av');
    fireEvent.keyDown(screen.getByLabelText('Prompt body'), { key: 'Escape' });
    expect(screen.queryByTestId('prompt-mention-picker')).toBeNull();

    // The token is gone, which releases the latch…
    await typeBody('plain text');
    expect(screen.queryByTestId('prompt-mention-picker')).toBeNull();
    // …and the next mention opens normally.
    await typeBody('plain text @');
    expect(screen.getByTestId('prompt-mention-picker')).toBeInTheDocument();
  });

  it('typing text without @ keeps the picker hidden', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('hello world');
    expect(screen.queryByTestId('prompt-mention-picker')).toBeNull();
  });

  it('removing the @ from the text closes the picker', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@foo');
    expect(screen.getByTestId('prompt-mention-picker')).toBeInTheDocument();

    await typeBody('foo');
    expect(screen.queryByTestId('prompt-mention-picker')).toBeNull();
  });

  it('a footer states how many candidates the active tab has', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');
    await screen.findAllByTestId('mention-asset-option');
    expect(screen.getByTestId('mention-count').textContent).toBe('2 results');
  });
});

describe('PromptNodeView — the Assets tab', () => {
  it('renders a thumbnail grid from the wire rows, cover or type icon', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');
    const options = await screen.findAllByTestId('mention-asset-option');

    expect(options).toHaveLength(2);
    expect(options[0]).toHaveTextContent('Ava');
    expect(options[1]).toHaveTextContent('Back Alley');
    // A cover renders as a picture; a coverless asset falls back to its type
    // icon rather than a blank tile.
    expect(options[0].querySelector('img')).not.toBeNull();
    expect(options[1].querySelector('img')).toBeNull();
    expect(options[1].querySelector('svg')).not.toBeNull();
  });

  it('asks for the library, not everything the scope can see', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');
    await screen.findAllByTestId('mention-asset-option');

    // `library: 'in'` is the load-bearing argument, and it is the user's
    // ruling (mig 449): a library member is one somebody ADDED. Script
    // imports and the rows the legacy-card migration created were not.
    expect(searchAssets).toHaveBeenCalledWith(
      SCOPE,
      expect.objectContaining({ library: 'in' }),
    );
  });

  it('the @query seeds the search, and the In Library toggle widens it', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@av');
    await waitFor(() =>
      expect(searchAssets).toHaveBeenCalledWith(
        SCOPE,
        expect.objectContaining({ q: 'av', library: 'in' }),
      ),
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId('mention-library-toggle'));
    });
    await waitFor(() =>
      expect(searchAssets).toHaveBeenCalledWith(
        SCOPE,
        expect.objectContaining({ library: 'all' }),
      ),
    );
  });

  it('a type chip filters the request', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');
    await screen.findAllByTestId('mention-asset-option');

    const chip = screen
      .getAllByTestId('mention-type-chip')
      .find((el) => el.getAttribute('data-type') === 'location');
    await act(async () => {
      fireEvent.click(chip!);
    });

    await waitFor(() =>
      expect(searchAssets).toHaveBeenCalledWith(
        SCOPE,
        expect.objectContaining({ type: 'location' }),
      ),
    );
  });

  it('a failed listing says so instead of showing an empty library', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    searchAssets.mockRejectedValue(new Error('network down'));
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');
    expect(await screen.findByTestId('mention-assets-error')).toBeInTheDocument();
  });

  it('the preview key opens a lightbox without inserting a mention', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');
    await screen.findAllByTestId('mention-asset-option');

    await act(async () => {
      fireEvent.mouseDown(screen.getByTestId('mention-asset-preview'));
    });

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(nodeData().mentioned_assets ?? []).toEqual([]);
  });
});

describe('PromptNodeView — inserting an asset mention', () => {
  it('writes a chip, the stable token and the node record — and no new node', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@av');
    const options = await screen.findAllByTestId('mention-asset-option');
    await act(async () => {
      fireEvent.mouseDown(options[0]);
    });

    const chip = await screen.findByTestId('prompt-asset-chip');
    expect(chip).toHaveTextContent('Ava');

    await waitFor(() => {
      expect(nodeData().body as string).toContain(`@[asset:${AVA.id}]`);
    });
    expect(nodeData().mentioned_assets).toEqual([
      {
        asset_id: AVA.id,
        name: 'Ava',
        asset_type: 'character',
        cover_file_id: AVA.cover_file_id,
        // The PRIMARY slot only — the `worn` file belongs to an outfit and a
        // mention binds none. Display-only; the run re-asks at dispatch.
        ref_resource_ids: ['727145299382534401'],
      },
    ]);
    expect(fetchAssetDetail).toHaveBeenCalledWith(SCOPE, AVA.id);
    // The ruling: a mention creates NO node.
    expect(useCanvasCoreStore.getState().nodes).toHaveLength(1);
  });

  it('a failed detail fetch still inserts the mention, and says so in the log', async () => {
    // The chip is fully functional without the snapshot — the run fetches the
    // detail again at dispatch. Refusing to insert would cost the user their
    // mention over a display detail; inserting silently with a made-up span
    // would be the confidently-wrong badge. It inserts, logs, and the strip
    // renders the span as unknown.
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    fetchAssetDetail.mockRejectedValue(new Error('detail down'));
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');
    const options = await screen.findAllByTestId('mention-asset-option');
    await act(async () => {
      fireEvent.mouseDown(options[0]);
    });

    await waitFor(() =>
      expect((nodeData().mentioned_assets as unknown[]).length).toBe(1),
    );
    expect(nodeData().mentioned_assets).toEqual([
      {
        asset_id: AVA.id,
        name: 'Ava',
        asset_type: 'character',
        cover_file_id: AVA.cover_file_id,
      },
    ]);
    // ABSENT, not `[]` — "not asked" is a different answer from "no reference
    // images", and the strip renders the two differently.
    expect(
      (nodeData().mentioned_assets as Array<Record<string, unknown>>)[0],
    ).not.toHaveProperty('ref_resource_ids');
    expect(errors).toHaveBeenCalled();
  });

  it('closes the picker after a pick', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');
    const options = await screen.findAllByTestId('mention-asset-option');
    await act(async () => {
      fireEvent.mouseDown(options[0]);
    });

    expect(screen.queryByTestId('prompt-mention-picker')).toBeNull();
  });

  it('the same asset mentioned twice is one delivery', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    for (const _ of [0, 1]) {
      await typeBody('@');
      const options = await screen.findAllByTestId('mention-asset-option');
      await act(async () => {
        fireEvent.mouseDown(options[0]);
      });
    }

    await waitFor(() =>
      expect((nodeData().mentioned_assets as unknown[]).length).toBe(1),
    );
  });

  it('deleting the chip prunes the record — the document IS the list', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');
    const options = await screen.findAllByTestId('mention-asset-option');
    await act(async () => {
      fireEvent.mouseDown(options[0]);
    });
    await waitFor(() =>
      expect((nodeData().mentioned_assets as unknown[]).length).toBe(1),
    );

    await act(async () => {
      fireEvent.click(screen.getByLabelText('Remove Ava'));
    });

    await waitFor(() => {
      expect(nodeData().mentioned_assets).toEqual([]);
      expect(nodeData().body as string).not.toContain('@[asset:');
    });
  });

  it('arrow keys move the active row and Enter inserts it', async () => {
    const data = seedPromptNode('p1');
    renderPromptNode('p1', data);

    await typeBody('@');
    await screen.findAllByTestId('mention-asset-option');

    const body = screen.getByLabelText('Prompt body');
    await act(async () => {
      fireEvent.keyDown(body, { key: 'ArrowDown' });
    });
    await act(async () => {
      fireEvent.keyDown(body, { key: 'Enter' });
    });

    await waitFor(() =>
      expect(nodeData().mentioned_assets).toEqual([
        {
          asset_id: ALLEY.id,
          name: 'Back Alley',
          asset_type: 'location',
          cover_file_id: null,
          // The stub answers Ava's detail for any id — what this case is about
          // is WHICH row Enter committed, not whose files came back.
          ref_resource_ids: ['727145299382534401'],
        },
      ]),
    );
  });
});

describe('PromptNodeView — a mention on the input strip', () => {
  it('a mentioned asset is a reference thumbnail and counts as an input', () => {
    const data = seedPromptNode('p1', {
      // The token has to be in the body: `mentioned_assets` is derived from the
      // document, so a record with no token is pruned on mount.
      body: `a shot of @[asset:${AVA.id}]`,
      mentioned_assets: [
        {
          asset_id: AVA.id,
          name: 'Ava',
          asset_type: 'character',
          cover_file_id: AVA.cover_file_id,
          ref_resource_ids: ['900001'],
        },
      ],
    });
    renderPromptNode('p1', data);

    const row = screen.getByTestId('prompt-input-row');
    const thumb = within(row).getByTestId('prompt-mention-thumb');
    expect(thumb.getAttribute('data-asset-id')).toBe(AVA.id);
    expect(thumb.getAttribute('data-position')).toBe('1');
    expect(thumb.querySelector('img')).not.toBeNull();
    expect(row.textContent).toContain('1 inputs');
  });

  it('restores the chip from the persisted token, in place', async () => {
    const data = seedPromptNode('p1', {
      body: `a shot of @[asset:${AVA.id}] at dusk`,
      mentioned_assets: [
        {
          asset_id: AVA.id,
          name: 'Ava',
          asset_type: 'character',
          cover_file_id: AVA.cover_file_id,
        },
      ],
    });
    renderPromptNode('p1', data);

    const chip = await screen.findByTestId('prompt-asset-chip');
    expect(chip).toHaveTextContent('Ava');
    // And the raw token is not left sitting in the box beside it.
    expect(screen.getByLabelText('Prompt body').textContent).not.toContain('@[asset:');
  });

  it('a failed mention bundle from the last run is reported, not swallowed', () => {
    const data = seedPromptNode('p1', {
      body: `a shot of @[asset:${AVA.id}]`,
      mentioned_assets: [
        { asset_id: AVA.id, name: 'Ava', asset_type: 'character', cover_file_id: null },
      ],
      last_mention_error: 'no_scope',
    });
    renderPromptNode('p1', data);

    const badge = screen.getByTestId('mention-bundle-error');
    expect(badge.getAttribute('title')).toBe('no_scope');
  });
});

describe('PromptNodeView — persisted resource refs (unchanged)', () => {
  it('ref chip renders for each resource_refs entry', async () => {
    const data = seedPromptNode('p1', {
      resource_refs: [
        {
          resource_id: 'r1',
          name: 'robot-concept.jpg',
          kind: 'image',
          mime: 'image/jpeg',
          scope: { type: 'personal', id: 'u1' },
        },
      ],
    });
    renderPromptNode('p1', data);

    expect(screen.getByTestId('prompt-ref-chips')).toBeInTheDocument();
    expect(screen.getByTestId('prompt-ref-chip')).toHaveTextContent(
      '@robot-concept.jpg',
    );
  });

  it('clicking × on a ref chip removes the ref from node data', async () => {
    const data = seedPromptNode('p1', {
      resource_refs: [
        {
          resource_id: 'r1',
          name: 'robot-concept.jpg',
          kind: 'image',
          mime: 'image/jpeg',
          scope: { type: 'personal', id: 'u1' },
        },
      ],
    });
    renderPromptNode('p1', data);

    fireEvent.mouseDown(
      screen.getByLabelText('Remove reference to robot-concept.jpg'),
    );

    expect((nodeData().resource_refs as unknown[])).toHaveLength(0);
  });

  it('inline edits still do not push onto the undo stack', async () => {
    const data = seedPromptNode('p1');
    expect(useCanvasCoreStore.getState().canUndo()).toBe(false);
    renderPromptNode('p1', data);

    await typeBody('some text');
    expect(useCanvasCoreStore.getState().canUndo()).toBe(false);
  });
});
