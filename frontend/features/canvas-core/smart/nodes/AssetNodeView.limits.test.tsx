// features/canvas-core/smart/nodes/AssetNodeView.limits.test.tsx
//
// The provider reference ceiling on the card's checklist (P4 Task 5, plan
// ruling E), and the report of what the last run's bundle would not send.
//
// Two properties do the work here:
//
//   * `null` capabilities mean NO limit. The hook documents `null` as
//     "unknown ⇒ render full support", and it is what a still-loading fetch, a
//     failed one, an old backend and an unlisted model all produce. Reading it
//     as zero would grey out every row on the day the endpoint hiccups.
//   * a CHECKED row is never disabled. Disabling it would trap the selection at
//     the ceiling with no way down, and `patchNode` writes no history entry, so
//     there is no undo either.

import { ReactFlowProvider } from '@xyflow/react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { AssetRowDetail } from '../../../../services/assetsService';
import { AssetNodeView } from './AssetNodeView';
import { _resetModelCapabilitiesCache } from './useModelCapabilities';

const fetchAssetDetail = vi.fn();
const listGenerationCapabilities = vi.fn();

vi.mock('../../../../services/assetsService', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    fetchAssetDetail: (...args: unknown[]) => fetchAssetDetail(...args),
  };
});

vi.mock('../../services/canvasGenerationService', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    listGenerationCapabilities: () => listGenerationCapabilities(),
  };
});

// The stub INTERPOLATES `{{name}}` placeholders, unlike the plain-defaults one
// in `AssetNodeView.test.tsx`: the badge's whole job is to say how many
// references went missing and why, and a stub that renders `{{count}}` would
// let a badge that never substitutes anything pass.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) => {
      const opts = (typeof fallback === 'object' && fallback !== null
        ? fallback
        : {}) as Record<string, unknown> & { defaultValue?: string };
      const template =
        typeof fallback === 'string' ? fallback : (opts.defaultValue ?? key);
      return template.replace(/\{\{(\w+)\}\}/g, (m, name) =>
        name in opts ? String(opts[name]) : m,
      );
    },
  }),
}));

const SCOPE = '727145299382534100';
const ASSET_ID = '727145299382534300';
// Named by SLOT, because the whole point of the fixture is that delivery order
// is not declaration order. `SLOTS.character` declares
// sheet → stills → expressions → extras → worn; `_slot_priority` delivers
// sheet → worn → stills → expressions → extras. A fixture holding only `sheet`
// and `stills` — the previous one — is the single arrangement where the two
// orders coincide, so it could not see the card ranking by the wrong one.
const SHEET = '900000000000000001';
const STILLS = '900000000000000002';
const EXPRESSIONS = '900000000000000003';
const WORN = '900000000000000004';

const file = (resource_id: string, slot: string, sort_order: number) => ({
  asset_id: ASSET_ID,
  resource_id,
  slot,
  loadout_id: null,
  sort_order,
  note: null,
  attached_by: null,
  attached_at: '2026-09-02T00:00:00+00:00',
});

const DETAIL = {
  id: ASSET_ID,
  scope_id: SCOPE,
  asset_type: 'character',
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
  cover_file_id: SHEET,
  source: 'manual',
  duplicated_from: null,
  is_system_preset: false,
  tags: {},
  sort_order: 0,
  created_by: null,
  created_at: '2026-09-01T00:00:00+00:00',
  updated_at: '2026-09-01T00:00:00+00:00',
  readiness: { state: 'ready', missing: [] },
  file_counts_by_slot: { sheet: 1, stills: 1, expressions: 1, worn: 1 },
  project_ids: [],
  loadout_count: 0,
  files: [
    // Deliberately supplied in DECLARATION order, so a card that simply keeps
    // the API's order renders the wrong one.
    file(SHEET, 'sheet', 0),
    file(STILLS, 'stills', 1),
    file(EXPRESSIONS, 'expressions', 2),
    file(WORN, 'worn', 3),
  ],
  links: [],
  linked_by: [],
  loadouts: [],
  used_in: { canvases: [], storyboards: [] },
} as unknown as AssetRowDetail;

const NODE_DATA = {
  asset_id: ASSET_ID,
  loadout_id: null as string | null,
  // Two checked, and NOT the two the declaration order would put first.
  selected_file_ids: [SHEET, WORN],
  name: 'Cole Bannon',
  asset_type: 'character' as const,
  cover_file_id: SHEET,
  readiness_state: 'ready' as const,
};

const baseProps = {
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

/** Downstream prompt on `model`, wired from the card. */
const promptNode = (id: string, model: string | undefined) => ({
  id,
  type: 'prompt',
  position: { x: 0, y: 0 },
  data: { body: '', gen: model ? { kind: 'image', model, count: 1 } : null },
});

function seedAndRender(
  data: Record<string, unknown> = NODE_DATA,
  downstream: Array<{ id: string; model?: string }> = [{ id: 'p1', model: 'codex' }],
) {
  useCanvasCoreStore.setState({
    kind: 'smart',
    canvasId: '9',
    loadStatus: 'ready',
    readOnly: false,
    nodes: [
      { id: 'a1', type: 'asset', position: { x: 0, y: 0 }, data },
      ...downstream.map((d) => promptNode(d.id, d.model)),
    ],
    connections: downstream.map((d) => ({ id: `e-${d.id}`, source: 'a1', target: d.id })),
    selection: [],
  } as never);
  return render(
    <MemoryRouter initialEntries={[`/team/${SCOPE}/canvas/9`]}>
      <Routes>
        <Route
          path="/team/:teamId/canvas/:canvasId"
          element={
            <ReactFlowProvider>
              <AssetNodeView {...baseProps} id="a1" type="asset" data={data} />
            </ReactFlowProvider>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

const box = (id: string) => screen.getByTestId(`asset-node-file-${id}`) as HTMLInputElement;
const row = (id: string) => screen.getByTestId(`asset-node-row-${id}`);

beforeEach(() => {
  useCanvasCoreStore.getState().reset();
  _resetModelCapabilitiesCache();
  fetchAssetDetail.mockResolvedValue(DETAIL);
  listGenerationCapabilities.mockResolvedValue({
    codex: {
      ratios: ['1:1'],
      quality: true,
      resolution: true,
      max_refs: 9,
      negative: true,
      video_modes: [],
    },
    'seedream-4': {
      ratios: ['1:1'],
      quality: false,
      resolution: false,
      max_refs: 2,
      negative: false,
      video_modes: [],
    },
    'ark-seedream': {
      ratios: ['1:1'],
      quality: false,
      resolution: false,
      max_refs: 0,
      negative: false,
      video_modes: [],
    },
    // A one-reference ceiling: the smallest number that can tell "rank within
    // the selection" apart from "rank within every file the asset owns".
    'ark-seedream-1ref': {
      ratios: ['1:1'],
      quality: false,
      resolution: false,
      max_refs: 1,
      negative: false,
      video_modes: [],
    },
  });
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('the checklist is drawn in DELIVERY order', () => {
  it('renders primary, then worn, then stills, then the declaration tail', () => {
    // `SLOTS.character` declares sheet → stills → expressions → extras → worn.
    // `_slot_priority` delivers sheet → worn → stills → expressions → extras,
    // and the bundle trims the tail of THAT. A card drawn in declaration order
    // dims the wrong rows; drawn in delivery order the ceiling is legible —
    // what the provider drops is exactly what is at the bottom.
    seedAndRender(NODE_DATA, [{ id: 'p1', model: 'codex' }]);
    return waitFor(() => {
      const ids = screen
        .getAllByRole('checkbox')
        .map((el) => el.getAttribute('data-testid'));
      expect(ids).toEqual([
        `asset-node-file-${SHEET}`,
        `asset-node-file-${WORN}`,
        `asset-node-file-${STILLS}`,
        `asset-node-file-${EXPRESSIONS}`,
      ]);
    });
  });
});

describe('over-limit greying', () => {
  it('greys the unchecked rows once the selection reaches the model ceiling', async () => {
    // seedream-4 takes 2; the card already has 2 checked.
    seedAndRender(NODE_DATA, [{ id: 'p1', model: 'seedream-4' }]);
    await waitFor(() => expect(box(STILLS)).toBeDisabled());
    expect(row(STILLS)).toHaveAttribute('data-over-limit', 'true');
    expect(box(EXPRESSIONS)).toBeDisabled();
    expect(screen.getByTestId('asset-node-refs-limit')).toBeInTheDocument();
  });

  it('never disables a CHECKED row — the user must be able to take it off', async () => {
    seedAndRender(NODE_DATA, [{ id: 'p1', model: 'seedream-4' }]);
    await waitFor(() => expect(box(STILLS)).toBeDisabled());
    expect(box(SHEET)).not.toBeDisabled();
    expect(box(WORN)).not.toBeDisabled();
  });

  it('marks the checked rows PAST the ceiling by DELIVERY rank, not list input order', async () => {
    // The I1 case, concretely. sheet + expressions + worn checked, ceiling 2.
    // The bundle sends sheet and worn and drops `expressions`, because `worn`
    // outranks it. Ranking by the declaration order instead dims `worn` — the
    // file that IS sent — and leaves `expressions` un-dimmed.
    seedAndRender({ ...NODE_DATA, selected_file_ids: [SHEET, EXPRESSIONS, WORN] }, [
      { id: 'p1', model: 'seedream-4' },
    ]);
    await waitFor(() =>
      expect(row(EXPRESSIONS)).toHaveAttribute('data-over-limit', 'true'),
    );
    expect(row(SHEET)).not.toHaveAttribute('data-over-limit');
    expect(row(WORN)).not.toHaveAttribute('data-over-limit');
    // Checked rows stay operable at the ceiling.
    expect(box(EXPRESSIONS)).not.toBeDisabled();
  });

  it('ranks within the SELECTION, so an unchecked leader costs no slot (C1)', async () => {
    // The population half of the alignment, and the half the earlier fix
    // missed. Ceiling 1, and the ONLY tick is on `expressions` — which ranks
    // FOURTH among the asset's files. Counting the whole file list would put
    // it at rank 3 and dim it as `over_limit`; counting the selection puts it
    // at rank 0 and it is delivered.
    //
    // The endpoint now answers the same way (`build_bundle` trims within
    // `selected_file_ids`), so this assertion and the run agree. Before C1 the
    // card said "will be sent" here while the run shipped NOTHING and the
    // post-run badge blamed the provider's limit.
    seedAndRender({ ...NODE_DATA, selected_file_ids: [EXPRESSIONS] }, [
      { id: 'p1', model: 'ark-seedream-1ref' },
    ]);
    await waitFor(() => expect(box(SHEET)).toBeDisabled());
    expect(row(EXPRESSIONS)).not.toHaveAttribute('data-over-limit');
    expect(box(EXPRESSIONS)).not.toBeDisabled();
    // The unchecked higher-priority rows are blocked because the ceiling is
    // FULL, which is a different statement from "this pick will be dropped".
    expect(row(SHEET)).toHaveAttribute('data-over-limit', 'true');
  });

  it('greys every unchecked row for a model that takes NO references', async () => {
    seedAndRender({ ...NODE_DATA, selected_file_ids: [] }, [
      { id: 'p1', model: 'ark-seedream' },
    ]);
    await waitFor(() => expect(box(SHEET)).toBeDisabled());
    expect(box(WORN)).toBeDisabled();
    expect(box(STILLS)).toBeDisabled();
    expect(box(EXPRESSIONS)).toBeDisabled();
  });

  it('imposes no limit when the model is generous', async () => {
    seedAndRender(NODE_DATA, [{ id: 'p1', model: 'codex' }]);
    await waitFor(() => expect(fetchAssetDetail).toHaveBeenCalled());
    expect(box(EXPRESSIONS)).not.toBeDisabled();
    expect(screen.queryByTestId('asset-node-refs-limit')).toBeNull();
  });

  it('imposes no limit when the capabilities fetch FAILS (null = unknown)', async () => {
    listGenerationCapabilities.mockRejectedValue(new Error('HTTP 500'));
    seedAndRender(NODE_DATA, [{ id: 'p1', model: 'seedream-4' }]);
    await waitFor(() => expect(fetchAssetDetail).toHaveBeenCalled());
    expect(box(EXPRESSIONS)).not.toBeDisabled();
    expect(screen.queryByTestId('asset-node-refs-limit')).toBeNull();
  });

  it('imposes no limit when the card feeds nothing', async () => {
    seedAndRender(NODE_DATA, []);
    await waitFor(() => expect(fetchAssetDetail).toHaveBeenCalled());
    expect(box(EXPRESSIONS)).not.toBeDisabled();
  });

  it('imposes no limit when two downstream prompts disagree on the model', async () => {
    // One ceiling cannot describe two providers, and greying by the smaller one
    // would disable a file the other would in fact have been sent.
    seedAndRender(NODE_DATA, [
      { id: 'p1', model: 'seedream-4' },
      { id: 'p2', model: 'codex' },
    ]);
    await waitFor(() => expect(fetchAssetDetail).toHaveBeenCalled());
    expect(box(EXPRESSIONS)).not.toBeDisabled();
    expect(screen.queryByTestId('asset-node-refs-limit')).toBeNull();
  });
});

describe('the last run’s bundle report', () => {
  it('shows what the bundle would not send, grouped by reason', async () => {
    seedAndRender({
      ...NODE_DATA,
      last_bundle_dropped: [
        { resource_id: STILLS, reason: 'over_limit' },
        { resource_id: EXPRESSIONS, reason: 'over_limit' },
      ],
    });
    const badge = await screen.findByTestId('asset-node-dropped');
    expect(badge).toHaveTextContent('2');
    expect(badge.getAttribute('title')).toContain(STILLS);
  });

  it('renders an unrecognised reason code as itself rather than omitting it', async () => {
    seedAndRender({
      ...NODE_DATA,
      last_bundle_dropped: [{ resource_id: STILLS, reason: 'brand_new_reason' }],
    });
    const badge = await screen.findByTestId('asset-node-dropped');
    expect(badge).toHaveTextContent('brand_new_reason');
  });

  it('shows nothing when the last run dropped nothing', async () => {
    seedAndRender({ ...NODE_DATA, last_bundle_dropped: [] });
    await waitFor(() => expect(fetchAssetDetail).toHaveBeenCalled());
    expect(screen.queryByTestId('asset-node-dropped')).toBeNull();
    expect(screen.queryByTestId('asset-node-bundle-error')).toBeNull();
  });

  it.each([
    ['no_model', 'Pick a model on the prompt'],
    ['no_scope', 'Open this canvas from a workspace'],
  ])('names %s as its own cause, not as an unreadable asset', async (code, text) => {
    // The generic "could not read this asset" line sends the user to inspect a
    // card that is fine. These two codes are about the PROMPT and the URL.
    seedAndRender({ ...NODE_DATA, last_bundle_error: code }, [
      { id: 'p1', model: 'codex' },
    ]);
    await waitFor(() =>
      expect(screen.getByTestId('asset-node-bundle-error')).toBeInTheDocument(),
    );
    const box = screen.getByTestId('asset-node-bundle-error');
    expect(box.textContent).toContain(text);
    expect(box.textContent).not.toContain('could not read this asset');
    expect(box).toHaveAttribute('data-bundle-error', code);
  });

  it('says the run could not read the asset at all, separately from a clean drop list', async () => {
    seedAndRender({
      ...NODE_DATA,
      last_bundle_dropped: [],
      last_bundle_error: 'HTTP 500',
    });
    const err = await screen.findByTestId('asset-node-bundle-error');
    expect(err.getAttribute('title')).toBe('HTTP 500');
    expect(screen.queryByTestId('asset-node-dropped')).toBeNull();
  });
});
