import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
vi.mock('../../services/searchService', () => ({
  getVectorsStatus: (...args: unknown[]) => getVectorsStatusMock(...args),
}));
vi.mock('../../services/aiService', () => ({
  backfillEmbeddings: (...args: unknown[]) => backfillMock(...args),
}));

// Real wire shape of GET /api/v1/search/vectors/status (PR 2): ids are numbers.
const OK_STATUS = {
  status: 'ok',
  space: {
    id: 1,
    actual_model: 'doubao-embedding-vision-251215',
    protocol: 'ark-multimodal',
    dims: 2048,
    modalities: ['image', 'text', 'video'],
    instruction_version: 'en_keyword_v1',
  },
  layers: [
    { layer: 'semantic', status: 'ok', covered: 20, total: 1409 },
    { layer: 'transcript', status: 'not_built', covered: 0, total: 1409 },
  ],
};

describe('VectorsPanel', () => {
  beforeEach(() => {
    getVectorsStatusMock.mockReset();
    backfillMock.mockReset();
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
    expect(screen.getByRole('button', { name: 'Dry Run' })).toBeEnabled();
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
    expect(screen.getByRole('button', { name: 'Dry Run' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Run 20' })).toBeDisabled();
  });

  it('disabled backfill buttons explain why on hover', async () => {
    getVectorsStatusMock.mockResolvedValue({
      status: 'unconfigured',
      space: null,
      layers: [{ layer: 'semantic', status: 'not_built', covered: 0, total: 1409 }],
    });
    render(<VectorsPanel />);
    const dry = await screen.findByRole('button', { name: 'Dry Run' });
    expect(dry).toHaveAttribute('title', 'Configure an embedding model first');
    expect(screen.getByRole('button', { name: 'Run 20' })).toHaveAttribute(
      'title',
      'Configure an embedding model first',
    );
  });

  it('enabled backfill buttons carry no disabled hint', async () => {
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    const dry = await screen.findByRole('button', { name: 'Dry Run' });
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
    expect(screen.getByRole('button', { name: 'Dry Run' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Run 20' })).toBeEnabled();

    backfillMock.mockResolvedValueOnce({
      success: true, dry_run: true, reembedded: [1], dispatched: [], skipped: [],
      in_flight: 0, remaining: 10, total_missing: 10,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Dry Run' }));
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

  it('Dry Run calls backfill with dry_run=true; Run 20 calls with limit 20, lists skipped by reason and refetches status', async () => {
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    await screen.findByText('doubao-embedding-vision-251215');

    backfillMock.mockResolvedValueOnce({
      success: true, dry_run: true, reembedded: [1, 2], dispatched: [], skipped: [],
      in_flight: 0, remaining: 1389, total_missing: 1389,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Dry Run' }));
    expect(await screen.findByText('2 would be embedded · 1,389 remaining')).toBeInTheDocument();
    expect(backfillMock).toHaveBeenLastCalledWith({ limit: 20, dry_run: true });
    expect(getVectorsStatusMock).toHaveBeenCalledTimes(1);

    backfillMock.mockResolvedValueOnce({
      success: true, dry_run: false, reembedded: [1], dispatched: [],
      skipped: [{ resource_id: 2, reason: 'empty_text' }],
      in_flight: 0, remaining: 1388, total_missing: 1389,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Run 20' }));
    expect(
      await screen.findByText('1 embedded · 1 skipped (empty_text ×1) · 1,388 remaining'),
    ).toBeInTheDocument();
    expect(backfillMock).toHaveBeenLastCalledWith({ limit: 20, dry_run: false });
    await waitFor(() => expect(getVectorsStatusMock).toHaveBeenCalledTimes(2));
  });

  it('aggregates skipped reasons and reports dispatched separately', async () => {
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    await screen.findByText('doubao-embedding-vision-251215');
    backfillMock.mockResolvedValueOnce({
      success: true, dry_run: false, reembedded: [],
      dispatched: [{ resource_id: 5, task_id: 't1' }, { resource_id: 6, task_id: 't2' }],
      skipped: [
        { resource_id: 7, reason: 'no_cover_url' },
        { resource_id: 8, reason: 'no_cover_url' },
        { resource_id: 9, reason: 'analysis_row_missing' },
      ],
      in_flight: 0, remaining: 1389, total_missing: 1389,
    });
    fireEvent.click(screen.getByRole('button', { name: 'Run 20' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Run 20' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Dry Run' }));
    expect(await screen.findByTestId('vectors-backfill-error')).toHaveTextContent(
      'Vector store not migrated yet. Backfill is unavailable.',
    );
    spy.mockRestore();
  });

  it('Visual / Camera rows read Arrives with PR 3 and have no buttons; Add Space is a disabled placeholder', async () => {
    getVectorsStatusMock.mockResolvedValue(OK_STATUS);
    render(<VectorsPanel />);
    await screen.findByText('doubao-embedding-vision-251215');
    for (const layer of ['visual', 'camera']) {
      const row = screen.getByTestId(`vector-layer-${layer}`);
      expect(row).toHaveTextContent('Arrives with PR 3');
      expect(row).toHaveTextContent('— / 1,409');
      expect(row.querySelectorAll('button')).toHaveLength(0);
    }
    expect(screen.getByTestId('vector-layer-transcript')).toHaveTextContent('Not built · Phase 2');
    const add = screen.getByRole('button', { name: 'Add Space' });
    expect(add).toBeDisabled();
    expect(add).toHaveAttribute('title', 'Arrives with space switching');
  });
});
