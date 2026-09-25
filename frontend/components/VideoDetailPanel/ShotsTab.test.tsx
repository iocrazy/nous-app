/**
 * ShotsTab — the three states of one video's shot index.
 *
 * Boundary mocks carry the REAL wire shapes: `GET /resources/{id}/shots`
 * (string Snowflake ids, ms ints, `index.covered` / `stale`), the 202 of
 * `POST /ai/analyze/index-shots/{id}`, and refusals as the production
 * ErrorResponse envelope (typed code in `details.code`). Task Center rows are
 * `task_tracking` rows as the realtime channel delivers them.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, def?: unknown, opts?: Record<string, unknown>) => {
      const text = typeof def === 'string' ? def : _k;
      const vars = typeof def === 'object' && def ? (def as Record<string, unknown>) : opts;
      return text.replace(/{{(\w+)}}/g, (_m, name) => String(vars?.[name] ?? ''));
    },
  }),
}));

const tm = vi.hoisted(() => ({ tasks: [] as unknown[], cancelTask: vi.fn() }));
vi.mock('../../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ tasks: tm.tasks, cancelTask: tm.cancelTask }),
}));

const svc = vi.hoisted(() => ({ getResourceShots: vi.fn(), indexShots: vi.fn() }));
vi.mock('../../services/shotsService', async () => {
  const actual = await vi.importActual<typeof import('../../services/shotsService')>(
    '../../services/shotsService',
  );
  return { ...actual, getResourceShots: svc.getResourceShots, indexShots: svc.indexShots };
});

import { ApiError } from '../../services/apiClient';
import { ShotsTab, estimateShots } from './ShotsTab';

const RID = '9007199254740993';

const NOT_INDEXED = { resource_id: RID, indexed: false, index: null, shots: [] };

const INDEXED = {
  resource_id: RID,
  indexed: true,
  index: {
    algo_version: 'hist_v1',
    shot_count: 3,
    duration_ms: 192000,
    indexed_at: '2026-09-30T08:12:44.120000+00:00',
    space_id: '352590227796039',
    covered: true,
    stale: false,
  },
  shots: [
    { id: '9007199254740994', shot_index: 0, start_ms: 0, end_ms: 33000, rep_frame_ms: 16500, cut_score: null },
    { id: '9007199254740995', shot_index: 1, start_ms: 33000, end_ms: 41000, rep_frame_ms: 37000, cut_score: 0.42 },
    { id: '9007199254740996', shot_index: 2, start_ms: 41000, end_ms: 192000, rep_frame_ms: 116500, cut_score: 0.61 },
  ],
};

const task = (over: Record<string, unknown>) => ({
  id: 'wf-1',
  user_id: 'u',
  task_type: 'index_shots',
  status: 'processing',
  title: 'Index shots · clip.mp4',
  subtitle: 'Embedding 26 / 42',
  progress: 62,
  resource_id: RID,
  metadata: {},
  created_at: '2026-09-30T08:00:00Z',
  ...over,
});

const envelope = (status: number, code: string, message: string) =>
  new ApiError(`${status}`, status, { code: `http_${status}`, details: { code, message } });

describe('estimateShots', () => {
  it('is one shot per 4.5 s, rounded up; null without a duration', () => {
    expect(estimateShots(446)).toBe(100);
    expect(estimateShots(4)).toBe(1);
    expect(estimateShots(0)).toBeNull();
    expect(estimateShots(undefined)).toBeNull();
  });
});

describe('ShotsTab', () => {
  beforeEach(() => {
    tm.tasks = [];
    tm.cancelTask.mockReset();
    svc.getResourceShots.mockReset().mockResolvedValue(NOT_INDEXED);
    svc.indexShots.mockReset();
  });

  it('A: shows the estimate (one embedding per shot) and a live Index This Video button', async () => {
    render(<ShotsTab resourceId={RID} durationSeconds={446} />);
    expect(await screen.findByTestId('shots-estimate')).toHaveTextContent('7:26 · ≈ 100 shots · ≈ 100 embeddings');
    const button = screen.getByRole('button', { name: 'Index This Video' });
    expect(button).toBeEnabled();
    expect(button).not.toHaveAttribute('title');
    expect(svc.getResourceShots).toHaveBeenCalledWith(RID);
  });

  it('A: without a resource the button is disabled and says why', async () => {
    render(<ShotsTab durationSeconds={60} />);
    const button = await screen.findByRole('button', { name: 'Index This Video' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'Not in the library yet');
    expect(svc.getResourceShots).not.toHaveBeenCalled();
  });

  it('A → B: a 202 switches to the progress view at once, then follows the task row', async () => {
    svc.indexShots.mockResolvedValue({ task_id: 'wf-1', workflow_id: 'wf-1', resource_id: RID });
    const { rerender } = render(<ShotsTab resourceId={RID} durationSeconds={446} />);
    await screen.findByRole('button', { name: 'Index This Video' });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Index This Video' }));
    });
    expect(svc.indexShots).toHaveBeenCalledWith(RID, { force: false });
    // Optimistic: the row has not reached the Task Center context yet.
    expect(screen.getByTestId('shots-indexing')).toBeInTheDocument();
    expect(screen.getByTestId('shots-subtitle')).toHaveTextContent('Queued');

    tm.tasks = [task({})];
    rerender(<ShotsTab resourceId={RID} durationSeconds={446} />);
    expect(screen.getByTestId('shots-subtitle')).toHaveTextContent('Embedding 26 / 42');
    expect(screen.getByTestId('shots-progress')).toHaveStyle({ width: '62%' });

    fireEvent.click(screen.getByTestId('shots-cancel'));
    expect(tm.cancelTask).toHaveBeenCalledWith('wf-1');
  });

  it('B → C: the followed task completing re-reads the index', async () => {
    svc.indexShots.mockResolvedValue({ task_id: 'wf-1', workflow_id: 'wf-1', resource_id: RID });
    const { rerender } = render(<ShotsTab resourceId={RID} durationSeconds={192} />);
    await screen.findByRole('button', { name: 'Index This Video' });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Index This Video' }));
    });
    svc.getResourceShots.mockResolvedValue(INDEXED);
    tm.tasks = [task({ status: 'completed', progress: 100, subtitle: 'Indexed · 3 shots' })];
    await act(async () => {
      rerender(<ShotsTab resourceId={RID} durationSeconds={192} />);
    });
    expect(await screen.findByTestId('shots-indexed')).toBeInTheDocument();
    expect(svc.getResourceShots).toHaveBeenCalledTimes(2);
  });

  it('B → A: a failed task shows its typed reason and offers Retry', async () => {
    svc.indexShots.mockResolvedValue({ task_id: 'wf-1', workflow_id: 'wf-1', resource_id: RID });
    const { rerender } = render(<ShotsTab resourceId={RID} durationSeconds={192} />);
    await screen.findByRole('button', { name: 'Index This Video' });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Index This Video' }));
    });
    tm.tasks = [
      task({
        status: 'failed',
        error_code: 'provider_error',
        error_msg: 'shot indexing failed: provider_error',
      }),
    ];
    await act(async () => {
      rerender(<ShotsTab resourceId={RID} durationSeconds={192} />);
    });
    expect(await screen.findByTestId('shots-error')).toHaveTextContent(
      'The embedding provider kept failing. Try again later.',
    );
    expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled();
  });

  it('B on mount: a task already running for this video is followed', async () => {
    tm.tasks = [task({ subtitle: 'Extracting frames', progress: 5 })];
    render(<ShotsTab resourceId={RID} durationSeconds={192} />);
    expect(await screen.findByTestId('shots-indexing')).toBeInTheDocument();
    expect(screen.getByTestId('shots-subtitle')).toHaveTextContent('Extracting frames');
  });

  it('A: a typed refusal reads under the button, not as a toast', async () => {
    svc.indexShots.mockRejectedValue(
      envelope(409, 'provider_no_image', 'The current embedding model cannot take images; …'),
    );
    render(<ShotsTab resourceId={RID} durationSeconds={192} />);
    await screen.findByRole('button', { name: 'Index This Video' });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Index This Video' }));
    });
    expect(screen.getByTestId('shots-error')).toHaveTextContent(
      'The current embedding model cannot take images. Pick an image-capable model in Settings → AI → Vectors.',
    );
    expect(screen.queryByTestId('shots-indexing')).toBeNull();
  });

  it('A: already_indexed is not an error — the tab re-reads and shows the index', async () => {
    svc.indexShots.mockRejectedValue(
      envelope(409, 'already_indexed', 'This video is already indexed in the current space; use Re-index to cut it again.'),
    );
    render(<ShotsTab resourceId={RID} durationSeconds={192} />);
    await screen.findByRole('button', { name: 'Index This Video' });
    svc.getResourceShots.mockResolvedValue(INDEXED);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Index This Video' }));
    });
    expect(await screen.findByTestId('shots-indexed')).toBeInTheDocument();
    expect(screen.queryByTestId('shots-error')).toBeNull();
  });

  it('C: index line, numbers, strip and list; clicks seek; the search hit is marked', async () => {
    svc.getResourceShots.mockResolvedValue(INDEXED);
    const onSeek = vi.fn();
    render(<ShotsTab resourceId={RID} durationSeconds={192} onSeek={onSeek} hitStartMs={48000} currentTimeSeconds={35} />);
    expect(await screen.findByTestId('shots-index-line')).toHaveTextContent('indexed 2026-09-30 · hist_v1');
    expect(screen.getByTestId('shots-stat-shots')).toHaveTextContent('3');
    expect(screen.getByTestId('shots-stat-vectors')).toHaveTextContent('3');
    expect(screen.getByTestId('shots-stat-average')).toHaveTextContent('64.0s');
    expect(screen.getByTestId('shots-stat-clusters')).toHaveTextContent('—');

    // Strip widths follow the shot lengths; the hit shot is outlined, the
    // playing shot (35 s → shot 2) is the current one.
    expect(screen.getByTestId('shot-seg-0')).toHaveStyle({ width: '17.1875%' });
    expect(screen.getByTestId('shot-seg-2')).toHaveAttribute('data-hit', 'true');
    expect(screen.getByTestId('shot-seg-1')).toHaveAttribute('data-current', 'true');
    expect(screen.getByTestId('shot-seg-0')).not.toHaveAttribute('data-hit');

    expect(screen.getByTestId('shot-row-2')).toHaveTextContent('Shot 3');
    expect(screen.getByTestId('shot-row-2')).toHaveTextContent('0:41 – 3:12');
    expect(screen.getByTestId('shot-row-2')).toHaveTextContent('search hit');
    expect(screen.getByTestId('shot-row-0')).not.toHaveTextContent('search hit');

    fireEvent.click(screen.getByTestId('shot-seg-1'));
    expect(onSeek).toHaveBeenLastCalledWith(33);
    fireEvent.click(screen.getByTestId('shot-row-2'));
    expect(onSeek).toHaveBeenLastCalledWith(41);
    expect(screen.queryByTestId('shots-not-covered')).toBeNull();
    expect(screen.queryByTestId('shots-stale')).toBeNull();
  });

  it('C: Re-index forces a new cut; a space without vectors and a stale cut say so', async () => {
    svc.getResourceShots.mockResolvedValue({
      ...INDEXED,
      index: { ...INDEXED.index, covered: false, stale: true, algo_version: 'hist_v0' },
    });
    svc.indexShots.mockResolvedValue({ task_id: 'wf-2', workflow_id: 'wf-2', resource_id: RID });
    render(<ShotsTab resourceId={RID} durationSeconds={192} />);
    expect(await screen.findByTestId('shots-not-covered')).toBeInTheDocument();
    expect(screen.getByTestId('shots-stale')).toBeInTheDocument();
    expect(screen.getByTestId('shots-stat-vectors')).toHaveTextContent('0');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Re-index' }));
    });
    expect(svc.indexShots).toHaveBeenCalledWith(RID, { force: true });
    await waitFor(() => expect(screen.getByTestId('shots-indexing')).toBeInTheDocument());
  });

  it('A: a failed read of the index is said, and indexing stays possible', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    svc.getResourceShots.mockRejectedValue(new ApiError('Server Error', 500, { code: 'http_500' }));
    render(<ShotsTab resourceId={RID} durationSeconds={192} />);
    expect(await screen.findByTestId('shots-load-failed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Index This Video' })).toBeEnabled();
    spy.mockRestore();
  });
});
