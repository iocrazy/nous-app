import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import en from '../../public/locales/en.json';
import { ApiError } from '../../services/apiClient';
import { VectorsPanel } from './VectorsPanel';

// Resolve against the shipped English copy so assertions read real sentences.
vi.mock('react-i18next', () => {
  const t = (key: string, opts?: Record<string, unknown>) => {
    const raw = key.split('.').reduce<unknown>((o, k) => (o as Record<string, unknown>)?.[k], en);
    let out = typeof raw === 'string' ? raw : key;
    for (const [k, v] of Object.entries(opts ?? {})) out = out.split(`{{${k}}}`).join(String(v));
    return out;
  };
  return { useTranslation: () => ({ t }) };
});

const getVectorsStatusMock = vi.fn();
const backfillMock = vi.fn();
const createSpaceMock = vi.fn();
const activateSpaceMock = vi.fn();
const deleteSpaceMock = vi.fn();
const getCatalogMock = vi.fn();
const setVisualSpaceMock = vi.fn();
const clearVisualSpaceMock = vi.fn();
const updateShotsPolicyMock = vi.fn();
vi.mock('../../services/searchService', () => ({
  getVectorsStatus: (...args: unknown[]) => getVectorsStatusMock(...args),
  createVectorSpace: (...args: unknown[]) => createSpaceMock(...args),
  activateVectorSpace: (...args: unknown[]) => activateSpaceMock(...args),
  deleteVectorSpace: (...args: unknown[]) => deleteSpaceMock(...args),
  getVectorSpaceCatalog: (...args: unknown[]) => getCatalogMock(...args),
  setVisualSpace: (...args: unknown[]) => setVisualSpaceMock(...args),
  clearVisualSpace: (...args: unknown[]) => clearVisualSpaceMock(...args),
  updateShotsPolicy: (...args: unknown[]) => updateShotsPolicyMock(...args),
}));
vi.mock('../../services/aiService', () => ({
  backfillEmbeddings: (...args: unknown[]) => backfillMock(...args),
}));
const backfillShotsMock = vi.fn();
vi.mock('../../services/shotsService', async () => {
  const actual = await vi.importActual<typeof import('../../services/shotsService')>(
    '../../services/shotsService',
  );
  return { ...actual, backfillShots: (...args: unknown[]) => backfillShotsMock(...args) };
});

// Real wire shape of POST /api/v1/search/... backfill-shots: ids are strings,
// `parent_task_id` is the flow id on a real run and null on a dry run.
const SHOTS_DRY_RUN = {
  success: true,
  dry_run: true,
  space_id: '352590227796039',
  total_pending: 1371,
  stale: 2,
  candidates: Array.from({ length: 20 }, (_, i) => String(9007199254740993 + i)),
  estimated_shots: 1120,
  estimated_tokens: 336000,
  parent_task_id: null,
  dispatched: [],
  skipped: [
    { resource_id: '9007199254741100', reason: 'no_video_file' },
    { resource_id: '9007199254741101', reason: 'no_video_file' },
    { resource_id: '9007199254741102', reason: 'already_indexed' },
  ],
};
const SHOTS_RUN = {
  ...SHOTS_DRY_RUN,
  dry_run: false,
  parent_task_id: '4e7c9a1e-0000-4000-8000-000000000001',
  dispatched: Array.from({ length: 18 }, (_, i) => `wf-${i}`),
  skipped: [{ resource_id: '9007199254741100', reason: 'dispatch_failed' }],
};

// Real wire shape of GET /api/v1/search/vectors/status: the Snowflake space id
// is a string; `stale` counts covered vectors the next backfill re-embeds.
const OK_STATUS = {
  status: 'ok',
  space: {
    id: '352590227796039',
    actual_model: 'doubao-embedding-vision-251215',
    protocol: 'ark-multimodal',
    dims: 2048,
    modalities: ['image', 'text', 'video'],
    instruction_version: 'en_keyword_v1',
  },
  layers: [
    { layer: 'semantic', status: 'ok', covered: 20, total: 1409, stale: 0 },
    { layer: 'visual', status: 'ok', covered: 38, total: 1200, stale: 2 },
    { layer: 'transcript', status: 'not_built', covered: 0, total: 1409, stale: 0 },
  ],
};

// Real shape with space switching: every space in `spaces` (ids are strings),
// the active one first here, then a candidate filled 150 of 200.
const ACTIVE_SPACE = {
  ...OK_STATUS.space,
  active: true,
  catalog_name: 'nous-doubao-embedding-vision',
  layers: OK_STATUS.layers,
};
const CANDIDATE_ID = '1234567890123456789';
const candidate = (covered: number, total = 200, catalog: string | null = 'nous-wemm-embedding-2b') => ({
  id: CANDIDATE_ID,
  actual_model: 'wemm-embedding-2b',
  protocol: 'openai-embeddings-chat',
  dims: 2048,
  modalities: ['text'],
  instruction_version: 'en_keyword_v1',
  active: false,
  catalog_name: catalog,
  layers: [
    { layer: 'semantic', status: covered ? 'ok' : 'not_built', covered, total, stale: 0 },
    { layer: 'transcript', status: 'not_built', covered: 0, total, stale: 0 },
  ],
});
const withSpaces = (cand: ReturnType<typeof candidate> | null, canManage = true) => ({
  ...OK_STATUS,
  spaces: cand ? [ACTIVE_SPACE, cand] : [ACTIVE_SPACE],
  can_manage: canManage,
});

// Real wire of the per-layer fields (PR #2482 / #2485, cn.nous.ink 2026-09-26):
// `visual_space` names the space the visual layer lives in, `spaces[].visual`
// marks it, `shots_policy` carries the automation policy + sweeper progress.
const FOLLOWING_VISUAL = {
  id: OK_STATUS.space.id,
  actual_model: 'doubao-embedding-vision-251215',
  catalog_name: 'nous-doubao-embedding-vision',
  follows_active: true,
};
const POLICY = {
  auto_index: 'local_only',
  backfill: 'off',
  batch: 5,
  daily_cap: 200,
  provider_local: false,
  dispatched_today: 0,
  active: 0,
  pending_total: 1162,
  last_tick: null,
  last_error: null,
  last_skip: null,
};
const imageCandidate = (visual = false) => ({
  ...candidate(150),
  modalities: ['image', 'text'],
  visual,
  layers: [
    ...candidate(150).layers,
    { layer: 'visual', status: visual ? 'ok' : 'not_built', covered: visual ? 38 : 0, total: 1118, stale: 0 },
  ],
});
const withVisual = (cand: ReturnType<typeof imageCandidate>, opts: { canManage?: boolean; policy?: typeof POLICY | null } = {}) => {
  const onCandidate = cand.visual;
  return {
    ...withSpaces(cand, opts.canManage ?? true),
    spaces: [{ ...ACTIVE_SPACE, visual: !onCandidate }, cand],
    visual_space: onCandidate
      ? { id: cand.id, actual_model: cand.actual_model, catalog_name: cand.catalog_name, follows_active: false }
      : FOLLOWING_VISUAL,
    visual_status: 'ok',
    shots_policy: opts.policy === undefined ? POLICY : opts.policy,
  };
};
// Real wire of GET /search/vectors/catalog: `last_test_status` carries the
// live status (backend test_platform_embedding_models_*), plus `engine`.
const ROWS = [
  { name: 'nous-doubao-embedding-vision', display_name: 'Doubao Embedding Vision', type: 'embedding', last_test_status: 'ok' },
  { name: 'nous-wemm-embedding-2b', display_name: 'WeMM Embedding 2B', type: 'embedding', last_test_status: 'ok' },
  { name: 'nous-wemm-embedding-4b', display_name: 'WeMM Embedding 4B', type: 'embedding', last_test_status: 'ok' },
];
const ENGINE_OK = { reachable: true, stale: false, checked_at: '2026-09-25T08:00:00Z' };
const EMBEDDING_MODELS = { models: ROWS, engine: ENGINE_OK };

describe('VectorsPanel', () => {
  beforeEach(() => {
    getVectorsStatusMock.mockReset();
    backfillMock.mockReset();
    createSpaceMock.mockReset();
    activateSpaceMock.mockReset();
    deleteSpaceMock.mockReset();
    getCatalogMock.mockReset();
    backfillShotsMock.mockReset();
    setVisualSpaceMock.mockReset();
    clearVisualSpaceMock.mockReset();
    updateShotsPolicyMock.mockReset();
  });

  it('renders the current space card from /vectors/status', async () => {
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    expect(await screen.findByText('doubao-embedding-vision-251215')).toBeInTheDocument();
    expect(screen.getByText('ark-multimodal')).toBeInTheDocument();
    expect(screen.getByText('2048 · halfvec · HNSW')).toBeInTheDocument();
    expect(screen.getByText('en_keyword_v1')).toBeInTheDocument();
    for (const m of ['image', 'text', 'video']) {
      expect(screen.getByTestId(`vector-capability-${m}`)).toHaveClass('text-ok');
    }
    expect(screen.getByText('20 / 1,409')).toBeInTheDocument();
    expect(within(screen.getByTestId('vector-layer-semantic')).getByRole('button', { name: 'Dry Run' })).toBeEnabled();
  });

  it('coverage shows stale vectors only when there are some', async () => {
    getVectorsStatusMock.mockResolvedValue({
      ...OK_STATUS,
      layers: [
        { layer: 'semantic', status: 'ok', covered: 20, total: 1409, stale: 7 },
        { layer: 'transcript', status: 'not_built', covered: 0, total: 1409, stale: 0 },
      ],
    });
    render(<VectorsPanel />);
    const semantic = await screen.findByTestId('vector-layer-semantic');
    expect(semantic).toHaveTextContent('20 / 1,409 · 7 stale');
    expect(screen.getByTestId('vector-layer-transcript')).not.toHaveTextContent('stale');
  });

  it('coverage omits the stale note at zero', async () => {
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    const semantic = await screen.findByTestId('vector-layer-semantic');
    expect(semantic).not.toHaveTextContent('stale');
  });

  it('unconfigured status shows the setup hint and disables backfill buttons', async () => {
    getVectorsStatusMock.mockResolvedValue({
      status: 'unconfigured',
      space: null,
      layers: [{ layer: 'semantic', status: 'not_built', covered: 0, total: 1409 }],
    });
    render(<VectorsPanel />);
    expect(
      await screen.findByText('No embedding model configured. Set one in Admin → AI Models.'),
    ).toBeInTheDocument();
    expect(within(screen.getByTestId('vector-layer-semantic')).getByRole('button', { name: 'Dry Run' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Run 200' })).toBeDisabled();
  });

  it('disabled backfill buttons explain why on hover', async () => {
    getVectorsStatusMock.mockResolvedValue({
      status: 'unconfigured',
      space: null,
      layers: [{ layer: 'semantic', status: 'not_built', covered: 0, total: 1409 }],
    });
    render(<VectorsPanel />);
    const dry = within(await screen.findByTestId('vector-layer-semantic')).getByRole('button', { name: 'Dry Run' });
    expect(dry).toHaveAttribute('title', 'Configure an embedding model first');
    expect(screen.getByRole('button', { name: 'Run 200' })).toHaveAttribute(
      'title',
      'Configure an embedding model first',
    );
  });

  it('enabled backfill buttons carry no disabled hint', async () => {
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    const dry = within(await screen.findByTestId('vector-layer-semantic')).getByRole('button', { name: 'Dry Run' });
    expect(dry).not.toHaveAttribute('title');
  });

  it('a backend without /vectors/status (404) still renders the table and keeps backfill usable', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    // Production envelope for an unknown route: ErrorResponse with code http_404.
    getVectorsStatusMock.mockRejectedValue(
      new ApiError('Not Found', 404, { code: 'http_404', details: null }),
    );
    render(<VectorsPanel />);
    expect(
      await screen.findByText('Vector status endpoint not available yet'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Could not load vector status')).toBeNull();
    expect(screen.getByTestId('vector-layer-semantic')).toBeInTheDocument();
    expect(within(screen.getByTestId('vector-layer-semantic')).getByRole('button', { name: 'Dry Run' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Run 200' })).toBeEnabled();

    backfillMock.mockResolvedValueOnce({
      success: true, dry_run: true, reembedded: ['1'], dispatched: [], skipped: [],
      in_flight: 0, remaining: 10, total_missing: 10, stale: 0,
    });
    fireEvent.click(within(screen.getByTestId('vector-layer-semantic')).getByRole('button', { name: 'Dry Run' }));
    expect(await screen.findByText('1 would be embedded · 10 remaining')).toBeInTheDocument();
    spy.mockRestore();
  });

  it('a non-404 status failure is still a load failure', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    getVectorsStatusMock.mockRejectedValue(new ApiError('Server Error', 500, { code: 'http_500' }));
    render(<VectorsPanel />);
    expect(await screen.findByText('Could not load vector status')).toBeInTheDocument();
    spy.mockRestore();
  });

  it('store_missing status shows the migration hint', async () => {
    getVectorsStatusMock.mockResolvedValue({ status: 'store_missing', space: null, layers: [] });
    render(<VectorsPanel />);
    expect(
      await screen.findByText('Vector store not migrated yet (migration 494).'),
    ).toBeInTheDocument();
  });

  it('shows a load failure line when the status request fails', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    getVectorsStatusMock.mockRejectedValue(new Error('boom'));
    render(<VectorsPanel />);
    expect(await screen.findByText('Could not load vector status')).toBeInTheDocument();
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it('Dry Run calls backfill with dry_run=true; Run 200 calls with limit 200, lists skipped by reason and refetches status', async () => {
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    await screen.findByText('doubao-embedding-vision-251215');

    backfillMock.mockResolvedValueOnce({
      success: true, dry_run: true, reembedded: ['352590227796039123', '352590227796039124'], dispatched: [], skipped: [],
      in_flight: 0, remaining: 1389, total_missing: 1389, stale: 0,
    });
    fireEvent.click(within(screen.getByTestId('vector-layer-semantic')).getByRole('button', { name: 'Dry Run' }));
    expect(await screen.findByText('2 would be embedded · 1,389 remaining')).toBeInTheDocument();
    expect(backfillMock).toHaveBeenLastCalledWith({ limit: 200, dry_run: true });
    expect(getVectorsStatusMock).toHaveBeenCalledTimes(1);

    backfillMock.mockResolvedValueOnce({
      success: true, dry_run: false, reembedded: ['352590227796039123'], dispatched: [],
      skipped: [{ resource_id: '352590227796039124', reason: 'empty_text' }],
      in_flight: 0, remaining: 1388, total_missing: 1389, stale: 0,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Run 200' }));
    expect(
      await screen.findByText('1 embedded · 1 skipped (empty_text ×1) · 1,388 remaining'),
    ).toBeInTheDocument();
    expect(backfillMock).toHaveBeenLastCalledWith({ limit: 200, dry_run: false });
    await waitFor(() => expect(getVectorsStatusMock).toHaveBeenCalledTimes(2));
  });

  it('reports relabelled legacy hashes apart from embedded rows', async () => {
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    await screen.findByText('doubao-embedding-vision-251215');
    backfillMock.mockResolvedValueOnce({
      success: true, dry_run: false, reembedded: ['1'], rehashed: 20, dispatched: [], skipped: [],
      in_flight: 0, remaining: 1368, total_missing: 1389, stale: 20,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Run 200' }));
    expect(
      await screen.findByText('1 embedded · 20 relabelled (no embedding call) · 1,368 remaining'),
    ).toBeInTheDocument();
  });

  it('aggregates skipped reasons and reports dispatched separately', async () => {
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    await screen.findByText('doubao-embedding-vision-251215');
    backfillMock.mockResolvedValueOnce({
      success: true, dry_run: false, reembedded: [],
      dispatched: [{ resource_id: '5', task_id: 't1' }, { resource_id: '6', task_id: 't2' }],
      skipped: [
        { resource_id: '7', reason: 'no_cover_url' },
        { resource_id: '8', reason: 'no_cover_url' },
        { resource_id: '9', reason: 'analysis_row_missing' },
      ],
      in_flight: 0, remaining: 1389, total_missing: 1389, stale: 0,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Run 200' }));
    expect(
      await screen.findByText(
        '0 embedded · 2 dispatched · 3 skipped (no_cover_url ×2, analysis_row_missing ×1) · 1,389 remaining',
      ),
    ).toBeInTheDocument();
  });

  it('shows a typed error line on 409 embedder_unconfigured instead of a toast', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    await screen.findByText('doubao-embedding-vision-251215');
    // Production ErrorResponse envelope: typed code lives in details.code.
    backfillMock.mockRejectedValueOnce(
      new ApiError('No embedding model configured', 409, {
        code: 'http_409',
        details: { code: 'embedder_unconfigured', message: 'No embedding model configured' },
      }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Run 200' }));
    const line = await screen.findByTestId('vectors-backfill-error');
    expect(line).toHaveTextContent('No embedding model configured. Set one in Admin → AI Models before backfilling.');
    expect(line).toHaveClass('text-danger');
    spy.mockRestore();
  });

  it('shows a typed error line on 503 vector_store_missing', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    await screen.findByText('doubao-embedding-vision-251215');
    backfillMock.mockRejectedValueOnce(
      new ApiError('Service Unavailable', 503, {
        code: 'http_503',
        details: { code: 'vector_store_missing' },
      }),
    );
    fireEvent.click(within(screen.getByTestId('vector-layer-semantic')).getByRole('button', { name: 'Dry Run' }));
    expect(await screen.findByTestId('vectors-backfill-error')).toHaveTextContent(
      'Vector store not migrated yet. Backfill is unavailable.',
    );
    spy.mockRestore();
  });

  it('Camera row reads Arrives with PR 4 and has no buttons; Add Space is admin-only', async () => {
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    await screen.findByText('doubao-embedding-vision-251215');
    const row = screen.getByTestId('vector-layer-camera');
    expect(row).toHaveTextContent('Arrives with PR 4');
    expect(row).toHaveTextContent('— / 1,409');
    expect(row.querySelectorAll('button')).toHaveLength(0);
    expect(screen.getByTestId('vector-layer-transcript')).toHaveTextContent('Not built · Phase 2');
    // An old backend (no can_manage) cannot be managed from here.
    const add = screen.getByRole('button', { name: 'Add Space' });
    expect(add).toBeDisabled();
    expect(add).toHaveAttribute('title', 'Only an admin can change embedding spaces');
  });

  describe('visual layer (shot backfill)', () => {
    const visualRow = () => screen.getByTestId('vector-layer-visual');
    const dryRun = () => within(visualRow()).getByRole('button', { name: 'Dry Run' });
    const run20 = () => within(visualRow()).getByRole('button', { name: 'Run 20' });

    it('reads coverage against VIDEOS with its stale count; Run 20 waits for a Dry Run', async () => {
      getVectorsStatusMock.mockResolvedValue(OK_STATUS);
      render(<VectorsPanel />);
      await screen.findByText('doubao-embedding-vision-251215');
      expect(visualRow()).toHaveTextContent('Visual · Frame');
      expect(visualRow()).toHaveTextContent('OK');
      expect(visualRow()).toHaveTextContent('38 / 1,200 · 2 stale');
      expect(visualRow()).toHaveTextContent('one keyframe per shot');
      expect(dryRun()).toBeEnabled();
      expect(run20()).toBeDisabled();
      expect(run20()).toHaveAttribute('title', 'Run a Dry Run first — every video is a task and every shot a paid embedding');
    });

    it('a backend without the visual row shows Not Built and no coverage', async () => {
      getVectorsStatusMock.mockResolvedValue({
        ...OK_STATUS,
        layers: OK_STATUS.layers.filter((l) => l.layer !== 'visual'),
      });
      render(<VectorsPanel />);
      await screen.findByText('doubao-embedding-vision-251215');
      expect(visualRow()).toHaveTextContent('Not Built');
      expect(within(visualRow()).getByText('—')).toBeInTheDocument();
    });

    it('Dry Run reports candidates, the shot / token estimate and the typed skips, then unlocks Run 20', async () => {
      getVectorsStatusMock.mockResolvedValue(OK_STATUS);
      backfillShotsMock.mockResolvedValue(SHOTS_DRY_RUN);
      render(<VectorsPanel />);
      await screen.findByText('doubao-embedding-vision-251215');
      fireEvent.click(dryRun());
      const line = await screen.findByTestId('vectors-shots-result');
      expect(backfillShotsMock).toHaveBeenCalledWith({ limit: 20, dry_run: true });
      expect(line).toHaveTextContent('Dry run · Visual');
      expect(line).toHaveTextContent('20 candidates · ≈ 1,120 shots · ≈ 336,000 tokens');
      expect(line).toHaveTextContent('3 skipped (no_video_file ×2, already_indexed ×1)');
      expect(line).toHaveTextContent('2 stale');
      expect(line).toHaveTextContent('1,371 remaining');
      expect(run20()).toBeEnabled();
      // A dry run creates nothing, so the status is not re-read.
      expect(getVectorsStatusMock).toHaveBeenCalledTimes(1);
    });

    it('Run 20 after a Dry Run dispatches, reports the batch and re-reads the status', async () => {
      getVectorsStatusMock.mockResolvedValue(OK_STATUS);
      backfillShotsMock.mockResolvedValueOnce(SHOTS_DRY_RUN).mockResolvedValueOnce(SHOTS_RUN);
      render(<VectorsPanel />);
      await screen.findByText('doubao-embedding-vision-251215');
      fireEvent.click(dryRun());
      await screen.findByTestId('vectors-shots-result');
      fireEvent.click(run20());
      await waitFor(() => expect(backfillShotsMock).toHaveBeenLastCalledWith({ limit: 20, dry_run: false }));
      const line = await screen.findByTestId('vectors-shots-result');
      await waitFor(() => expect(line).toHaveTextContent('Last backfill · Visual'));
      expect(line).toHaveTextContent('18 dispatched');
      expect(line).toHaveTextContent('1 skipped (dispatch_failed ×1)');
      expect(line).toHaveTextContent('1,353 remaining');
      expect(getVectorsStatusMock).toHaveBeenCalledTimes(2);
    });

    it('a refusal of the shot backfill reads as its typed line, separate from the semantic line', async () => {
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
      getVectorsStatusMock.mockResolvedValue(OK_STATUS);
      backfillShotsMock.mockRejectedValue(
        new ApiError('409 Conflict', 409, {
          code: 'http_409',
          details: { code: 'provider_no_image', message: 'The current embedding model cannot take images; …' },
        }),
      );
      render(<VectorsPanel />);
      await screen.findByText('doubao-embedding-vision-251215');
      fireEvent.click(dryRun());
      expect(await screen.findByTestId('vectors-shots-error')).toHaveTextContent(
        'The current embedding model cannot take images. Pick an image-capable model for the active space.',
      );
      expect(screen.queryByTestId('vectors-backfill-error')).toBeNull();
      expect(run20()).toBeDisabled();
      spy.mockRestore();
    });
  });

  describe('space switching', () => {
    it('renders a candidate card with its own coverage next to the active one', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(150)));
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      expect(card).toHaveTextContent('Candidate Space');
      expect(card).toHaveTextContent('wemm-embedding-2b');
      expect(card).toHaveTextContent('nous-wemm-embedding-2b');
      expect(card).toHaveTextContent('openai-embeddings-chat');
      expect(card).toHaveTextContent('150 / 200');
      // The active card and the layers table are untouched.
      expect(screen.getByText('doubao-embedding-vision-251215')).toBeInTheDocument();
      expect(screen.getByTestId('vector-layer-semantic')).toHaveTextContent('20 / 1,409');
    });

    it('Switch is disabled until the candidate covers everything, and says why', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(150)));
      render(<VectorsPanel />);
      const sw = await screen.findByRole('button', { name: 'Switch To This Space' });
      expect(sw).toBeDisabled();
      expect(sw).toHaveAttribute('title', 'Backfill to 100% before switching');
    });

    it('Switch is disabled on an empty library (0 of 0)', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(0, 0)));
      render(<VectorsPanel />);
      expect(await screen.findByRole('button', { name: 'Switch To This Space' })).toBeDisabled();
    });

    it('Switch at 100% activates the space and renders the returned status', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(200)));
      const switched = {
        status: 'ok',
        space: { ...candidate(200), active: undefined },
        layers: candidate(200).layers,
        spaces: [{ ...candidate(200), active: true }, { ...ACTIVE_SPACE, active: false }],
        can_manage: true,
      };
      activateSpaceMock.mockResolvedValue(switched);
      render(<VectorsPanel />);
      const sw = await screen.findByRole('button', { name: 'Switch To This Space' });
      expect(sw).toBeEnabled();
      fireEvent.click(sw);
      await waitFor(() => expect(activateSpaceMock).toHaveBeenCalledWith(CANDIDATE_ID));
      // doubao is now the candidate; its card appears.
      expect(await screen.findByTestId(`vector-candidate-${OK_STATUS.space.id}`)).toBeInTheDocument();
    });

    it('Backfill 200 on a candidate fills THAT space and refetches', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(150)));
      backfillMock.mockResolvedValueOnce({
        success: true, dry_run: false, reembedded: ['1', '2'], dispatched: [], skipped: [],
        in_flight: 0, remaining: 48, total_missing: 50, stale: 0,
      });
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      fireEvent.click(within(card).getByRole('button', { name: 'Backfill 200' }));
      await waitFor(() =>
        expect(backfillMock).toHaveBeenCalledWith({ limit: 200, dry_run: false, space_id: CANDIDATE_ID }),
      );
      expect(await within(card).findByText('2 embedded · 48 remaining')).toBeInTheDocument();
      await waitFor(() => expect(getVectorsStatusMock).toHaveBeenCalledTimes(2));
    });

    it('a candidate whose model left the catalog cannot be filled or switched to', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(200, 200, null)));
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      expect(card).toHaveTextContent('Not in catalog');
      const fill = within(card).getByRole('button', { name: 'Backfill 200' });
      expect(fill).toBeDisabled();
      expect(fill).toHaveAttribute('title', "This space's model is no longer in the catalog, so it cannot be filled");
      const sw = within(card).getByRole('button', { name: 'Switch To This Space' });
      expect(sw).toBeDisabled();
      expect(sw).toHaveAttribute('title', "This space's model is no longer in the catalog");
    });

    it('Add Space lists only catalog models that have no space yet, then creates one', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(150)));
      getCatalogMock.mockResolvedValue(EMBEDDING_MODELS);
      createSpaceMock.mockResolvedValue({ ...candidate(0), active: undefined });
      render(<VectorsPanel />);
      fireEvent.click(await screen.findByRole('button', { name: 'Add Space' }));
      // Platform rows only (GET /search/vectors/catalog), never the caller's BYOK rows.
      await waitFor(() => expect(getCatalogMock).toHaveBeenCalledTimes(1));
      const picker = await screen.findByTestId('vector-add-space-picker');
      expect(within(picker).queryByText('Doubao Embedding Vision')).toBeNull();
      expect(within(picker).queryByText('WeMM Embedding 2B')).toBeNull();
      fireEvent.click(within(picker).getByRole('button', { name: /WeMM Embedding 4B/ }));
      await waitFor(() => expect(createSpaceMock).toHaveBeenCalledWith('nous-wemm-embedding-4b'));
      await waitFor(() => expect(getVectorsStatusMock).toHaveBeenCalledTimes(2));
    });

    it('Add Space marks rows that are not ready and cannot pick a not-loaded one', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(null));
      getCatalogMock.mockResolvedValue({
        models: [
          { ...ROWS[1], last_test_status: 'idle' },
          { ...ROWS[2], last_test_status: 'not_probed' },
        ],
        engine: ENGINE_OK,
      });
      render(<VectorsPanel />);
      fireEvent.click(await screen.findByRole('button', { name: 'Add Space' }));
      const picker = await screen.findByTestId('vector-add-space-picker');
      const idle = within(picker).getByRole('button', { name: /WeMM Embedding 2B/ });
      expect(idle).toBeDisabled();
      expect(idle).toHaveTextContent('Not loaded on nous-engine');
      const unknown = within(picker).getByRole('button', { name: /WeMM Embedding 4B/ });
      expect(unknown).toBeEnabled();
      expect(unknown).toHaveTextContent('Status unknown');
      expect(within(picker).queryByTestId('vector-catalog-engine-unreachable')).toBeNull();
    });

    it('Add Space says so when nous-engine is unreachable or its answer is stale', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(null));
      getCatalogMock.mockResolvedValueOnce({ models: ROWS, engine: { ...ENGINE_OK, reachable: false } });
      const { unmount } = render(<VectorsPanel />);
      fireEvent.click(await screen.findByRole('button', { name: 'Add Space' }));
      expect(await screen.findByTestId('vector-catalog-engine-unreachable')).toHaveTextContent(
        'nous-engine is unreachable right now',
      );
      unmount();
      getCatalogMock.mockResolvedValueOnce({ models: ROWS, engine: { ...ENGINE_OK, stale: true } });
      render(<VectorsPanel />);
      fireEvent.click(await screen.findByRole('button', { name: 'Add Space' }));
      expect(await screen.findByTestId('vector-catalog-engine-stale')).toHaveTextContent('Status may be delayed');
    });

    it('a 422 dimension_mismatch from Add Space reads both widths', async () => {
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
      getVectorsStatusMock.mockResolvedValue(withSpaces(null));
      getCatalogMock.mockResolvedValue(EMBEDDING_MODELS);
      // Production ErrorResponse envelope: the typed body lives in details.
      createSpaceMock.mockRejectedValue(
        new ApiError('nous-wemm-embedding-4b returns 2560 dimensions', 422, {
          code: 'http_422',
          details: {
            code: 'dimension_mismatch',
            expected: 2048,
            got: 2560,
            model: 'nous-wemm-embedding-4b',
            message: 'nous-wemm-embedding-4b returns 2560 dimensions; the vector store holds 2048.',
          },
        }),
      );
      render(<VectorsPanel />);
      fireEvent.click(await screen.findByRole('button', { name: 'Add Space' }));
      const picker = await screen.findByTestId('vector-add-space-picker');
      fireEvent.click(within(picker).getByRole('button', { name: /WeMM Embedding 4B/ }));
      const line = await screen.findByTestId('vector-space-error');
      expect(line).toHaveTextContent('nous-wemm-embedding-4b returns 2560 dims, the store holds 2048');
      expect(line).toHaveClass('text-danger');
      spy.mockRestore();
    });

    it('non-admins see Add / Switch / Delete disabled with the reason', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(200), false));
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      for (const btn of [
        screen.getByRole('button', { name: 'Add Space' }),
        within(card).getByRole('button', { name: 'Switch To This Space' }),
        within(card).getByRole('button', { name: 'Delete' }),
      ]) {
        expect(btn).toBeDisabled();
        expect(btn).toHaveAttribute('title', 'Only an admin can change embedding spaces');
      }
      // Filling one's own coverage is not an admin action.
      expect(within(card).getByRole('button', { name: 'Backfill 200' })).toBeEnabled();
    });

    it('Delete asks inline, then deletes and reports the cascaded vectors', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(150)));
      deleteSpaceMock.mockResolvedValue({ deleted: true, space_id: CANDIDATE_ID, deleted_vectors: 187 });
      const confirmSpy = vi.spyOn(window, 'confirm');
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      fireEvent.click(within(card).getByRole('button', { name: 'Delete' }));
      expect(deleteSpaceMock).not.toHaveBeenCalled();
      expect(card).toHaveTextContent('Delete this space and every vector in it (all users)?');
      fireEvent.click(within(card).getByRole('button', { name: 'Confirm Delete' }));
      await waitFor(() => expect(deleteSpaceMock).toHaveBeenCalledWith(CANDIDATE_ID));
      expect(await screen.findByText('Space deleted · 187 vectors removed')).toBeInTheDocument();
      expect(confirmSpy).not.toHaveBeenCalled();
      confirmSpy.mockRestore();
    });

    it('Cancel closes the inline delete confirmation without deleting', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(150)));
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      fireEvent.click(within(card).getByRole('button', { name: 'Delete' }));
      fireEvent.click(within(card).getByRole('button', { name: 'Cancel' }));
      expect(within(card).queryByRole('button', { name: 'Confirm Delete' })).toBeNull();
      expect(deleteSpaceMock).not.toHaveBeenCalled();
    });

    it.each([
      ['catalog_model_disabled', 'This model is disabled in the catalog.'],
      ['not_an_embedding_model', 'This catalog model is not an embedding model.'],
      ['space_not_found', 'This space no longer exists. Refresh the page.'],
      ['vector_store_missing', 'Vector store not migrated yet. Space changes are unavailable.'],
      ['byok_row_not_allowed', 'A personal API key row cannot back a shared embedding space.'],
      ['active_space_unknown', 'Could not tell which space is active, so nothing was deleted. Try again.'],
    ])('typed %s reads its own line', async (code, text) => {
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(150)));
      deleteSpaceMock.mockRejectedValue(
        new ApiError('Refused', 409, { code: 'http_409', details: { code, message: 'server text' } }),
      );
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      fireEvent.click(within(card).getByRole('button', { name: 'Delete' }));
      fireEvent.click(within(card).getByRole('button', { name: 'Confirm Delete' }));
      const line = await screen.findByTestId('vector-space-error');
      expect(line).toHaveTextContent(text);
      spy.mockRestore();
    });
  });

  describe('visual layer space', () => {
    it('older backends without visual_space show no visual buttons or lines', async () => {
      getVectorsStatusMock.mockResolvedValue(withSpaces(candidate(150)));
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      expect(within(card).queryByRole('button', { name: 'Use For Visual' })).toBeNull();
      expect(screen.queryByTestId('vector-visual-layer-line')).toBeNull();
      expect(screen.getByTestId('vector-visual-source')).toHaveTextContent('one keyframe per shot');
      expect(screen.queryByTestId('indexing-policy')).toBeNull();
    });

    it('says the visual layer follows the current space, on the card and in the Visual row', async () => {
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(false)));
      render(<VectorsPanel />);
      expect(await screen.findByTestId('vector-visual-layer-line')).toHaveTextContent('follows current space');
      expect(screen.getByTestId('vector-visual-source')).toHaveTextContent(
        'one keyframe per shot · follows current space',
      );
    });

    it('Use For Visual points the visual layer at the candidate and renders the returned status', async () => {
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(false)));
      setVisualSpaceMock.mockResolvedValue(withVisual(imageCandidate(true)));
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      const btn = within(card).getByRole('button', { name: 'Use For Visual' });
      expect(btn).toBeEnabled();
      fireEvent.click(btn);
      await waitFor(() => expect(setVisualSpaceMock).toHaveBeenCalledWith(CANDIDATE_ID));
      expect(await screen.findByTestId('vector-visual-badge')).toHaveTextContent('Visual layer');
      expect(screen.getByTestId('vector-visual-layer-line')).toHaveTextContent('nous-wemm-embedding-2b · separate space');
      expect(screen.getByTestId('vector-visual-source')).toHaveTextContent('space nous-wemm-embedding-2b');
      expect(screen.getByTestId('vector-space-notice')).toHaveTextContent('nous-wemm-embedding-2b');
      // The candidate's own visual coverage is on its card now.
      expect(screen.getByTestId('vector-candidate-visual-coverage')).toHaveTextContent('38 / 1,118');
    });

    it('a text-only candidate cannot be used for the visual layer, and says why', async () => {
      const textOnly = { ...imageCandidate(false), modalities: ['text'] };
      getVectorsStatusMock.mockResolvedValue(withVisual(textOnly));
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      const btn = within(card).getByRole('button', { name: 'Use For Visual' });
      expect(btn).toBeDisabled();
      expect(btn).toHaveAttribute('title', "This space's model takes no image input");
    });

    it('the visual space shows Follow Current instead, and its Delete is disabled with the reason', async () => {
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(true)));
      clearVisualSpaceMock.mockResolvedValue(withVisual(imageCandidate(false)));
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      expect(within(card).queryByRole('button', { name: 'Use For Visual' })).toBeNull();
      const del = within(card).getByRole('button', { name: 'Delete' });
      expect(del).toBeDisabled();
      expect(del).toHaveAttribute('title', 'This space serves the Visual layer — point it elsewhere first');
      fireEvent.click(within(card).getByRole('button', { name: 'Follow Current' }));
      await waitFor(() => expect(clearVisualSpaceMock).toHaveBeenCalled());
      expect(await screen.findByText('Visual layer follows the current space again.')).toBeInTheDocument();
      expect(screen.queryByTestId('vector-visual-badge')).toBeNull();
    });

    it('a 422 provider_no_image from Use For Visual reads its own line', async () => {
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(false)));
      setVisualSpaceMock.mockRejectedValue(
        new ApiError('Refused', 422, {
          code: 'http_422',
          details: { code: 'provider_no_image', message: 'wemm-embedding-2b takes no image input' },
        }),
      );
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      fireEvent.click(within(card).getByRole('button', { name: 'Use For Visual' }));
      expect(await screen.findByTestId('vector-space-error')).toHaveTextContent("This space's model takes no image input");
      spy.mockRestore();
    });

    it('an unavailable visual space is said on the current-space card', async () => {
      getVectorsStatusMock.mockResolvedValue({
        ...withVisual(imageCandidate(false)),
        visual_space: null,
        visual_status: 'visual_space_unavailable',
      });
      render(<VectorsPanel />);
      expect(await screen.findByTestId('vector-visual-status')).toHaveTextContent('its model is no longer available');
    });

    it('non-admins see Use For Visual disabled with the admin reason', async () => {
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(false), { canManage: false }));
      render(<VectorsPanel />);
      const card = await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      const btn = within(card).getByRole('button', { name: 'Use For Visual' });
      expect(btn).toBeDisabled();
      expect(btn).toHaveAttribute('title', 'Only an admin can change embedding spaces');
    });
  });

  describe('indexing policy', () => {
    it('renders the policy, the progress line and the network-provider warning', async () => {
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(false)));
      render(<VectorsPanel />);
      const section = await screen.findByTestId('indexing-policy');
      expect(
        within(within(section).getByTestId('policy-auto-index')).getByRole('radio', { name: 'Local provider only', checked: true }),
      ).toBeInTheDocument();
      expect(within(section).getByTestId('policy-provider-line')).toHaveTextContent(
        'current visual provider is a network one — Local provider only equals Off',
      );
      // remaining = the Visual row's total − covered (1,200 − 38).
      expect(within(section).getByTestId('policy-today')).toHaveTextContent('0 dispatched · 0 running · 1,162 remaining');
      expect(within(section).getByTestId('policy-today')).toHaveTextContent('no tick yet');
      expect(within(section).getByRole('button', { name: 'Save Policy' })).toBeDisabled();
    });

    it('a local provider reads the free-indexing line and progress with a last tick', async () => {
      const policy = {
        ...POLICY,
        backfill: 'local_only',
        provider_local: true,
        dispatched_today: 37,
        active: 3,
        last_tick: '2026-09-26T10:20:00+00:00',
        last_skip: 'backpressure',
      };
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(true), { policy }));
      render(<VectorsPanel />);
      const section = await screen.findByTestId('indexing-policy');
      expect(within(section).getByTestId('policy-provider-line')).toHaveTextContent('visual provider is nous-engine (local)');
      expect(within(section).getByTestId('policy-provider-line')).not.toHaveClass('text-warn');
      const today = within(section).getByTestId('policy-today');
      expect(today).toHaveTextContent('37 dispatched · 3 running');
      expect(today).toHaveTextContent('last tick 2026-09-26 10:20');
      expect(today).toHaveTextContent('last tick skipped: backpressure');
    });

    it('Save sends only the changed fields and renders the returned status', async () => {
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(true)));
      const saved = { ...POLICY, backfill: 'local_only', batch: 8, provider_local: true };
      updateShotsPolicyMock.mockResolvedValue(withVisual(imageCandidate(true), { policy: saved }));
      render(<VectorsPanel />);
      const section = await screen.findByTestId('indexing-policy');
      fireEvent.click(within(within(section).getByTestId('policy-backfill')).getByRole('radio', { name: 'Local provider only' }));
      fireEvent.change(within(section).getByRole('spinbutton', { name: 'Batch per tick' }), { target: { value: '8' } });
      const save = within(section).getByRole('button', { name: 'Save Policy' });
      expect(save).toBeEnabled();
      fireEvent.click(save);
      await waitFor(() => expect(updateShotsPolicyMock).toHaveBeenCalledWith({ backfill: 'local_only', batch: 8 }));
      expect(await screen.findByTestId('policy-notice')).toHaveTextContent('Policy saved');
      expect(
        within(within(section).getByTestId('policy-backfill')).getByRole('radio', { name: 'Local provider only', checked: true }),
      ).toBeInTheDocument();
      expect(within(section).getByRole('button', { name: 'Save Policy' })).toBeDisabled();
    });

    it('batch is clamped to 1–50 in the field', async () => {
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(true)));
      render(<VectorsPanel />);
      const section = await screen.findByTestId('indexing-policy');
      const batch = within(section).getByRole('spinbutton', { name: 'Batch per tick' });
      fireEvent.change(batch, { target: { value: '500' } });
      expect(batch).toHaveValue(50);
    });

    it('a 422 policy_invalid names the field', async () => {
      const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(true)));
      updateShotsPolicyMock.mockRejectedValue(
        new ApiError('Refused', 422, {
          code: 'http_422',
          details: { code: 'policy_invalid', field: 'batch', message: 'an integer in [1, 50]' },
        }),
      );
      render(<VectorsPanel />);
      const section = await screen.findByTestId('indexing-policy');
      fireEvent.click(within(within(section).getByTestId('policy-auto-index')).getByRole('radio', { name: 'Always' }));
      fireEvent.click(within(section).getByRole('button', { name: 'Save Policy' }));
      expect(await screen.findByTestId('policy-error')).toHaveTextContent('Policy rejected: batch — an integer in [1, 50]');
      spy.mockRestore();
    });

    it('non-admins see the policy read-only', async () => {
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(true), { canManage: false }));
      render(<VectorsPanel />);
      const section = await screen.findByTestId('indexing-policy');
      expect(within(within(section).getByTestId('policy-auto-index')).getByRole('radio', { name: 'Off' })).toBeDisabled();
      expect(within(section).getByRole('spinbutton', { name: 'Batch per tick' })).toBeDisabled();
      expect(within(section).getByRole('button', { name: 'Save Policy' })).toBeDisabled();
      expect(section).toHaveTextContent('Only an admin can change the indexing policy');
    });

    it('a null shots_policy (unreadable on the server) hides the section', async () => {
      getVectorsStatusMock.mockResolvedValue(withVisual(imageCandidate(true), { policy: null }));
      render(<VectorsPanel />);
      await screen.findByTestId(`vector-candidate-${CANDIDATE_ID}`);
      expect(screen.queryByTestId('indexing-policy')).toBeNull();
    });
  });
});
