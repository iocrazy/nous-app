/**
 * useResourceProcessingFollowUps — the parts of F1's chain that only a
 * task-state watcher can finish.
 *
 * `ensureResourceProcessed` can only start the transcription; the summary
 * has to wait until the transcript exists (the backend literally answers
 * "trigger summary again once transcript is ready"). This hook watches the
 * Task Center for that moment.
 *
 * Two properties matter beyond "it fires": it must fire ONCE (a second
 * summary is a second charge), and it must degrade silently where there is
 * no TaskManagerProvider — the floating chat also mounts on the fullscreen
 * editor routes (RECON#18), and crashing the editor to chase a summary
 * would be a bad trade.
 */
import { renderHook } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const summaryMock = vi.fn();
const transcribeMock = vi.fn();
vi.mock('../services/aiService', () => ({
  triggerSummaryByResource: (id: string) => summaryMock(id),
  triggerTranscriptionByResource: (id: string) => transcribeMock(id),
}));

const notify = vi.fn();
const t = (_k: string, def: string, opts?: Record<string, unknown>) =>
  def.replace(/\{\{(\w+)\}\}/g, (_m, n) => String(opts?.[n] ?? ''));
const useFollowUpWithNotify = () => useResourceProcessingFollowUps({ notify, t });

const taskManagerMock = vi.fn();
vi.mock('./useOptionalTaskManager', () => ({
  useOptionalTaskManager: () => taskManagerMock(),
}));

import { useResourceProcessingFollowUps } from './useResourceProcessingFollowUps';
import {
  pendingAudioRetries,
  rememberPendingAudioRetry,
  rememberTranscriptionFollowUp,
  resetTranscriptionFollowUps,
  transcriptionFollowUp,
  transcriptionFollowUps,
} from '../utils/transcriptionFollowUp';

/** Real task_tracking wire shape: resource_id is a STRING column.
 *  `created_at` defaults to now because the hook only accepts tasks from
 *  the trigger instant onward — a hard-coded date would make every fixture
 *  a stale task the moment the suite outlives it. */
function task(over: Record<string, unknown> = {}) {
  return {
    id: 'wf-1',
    task_type: 'ai_transcription',
    status: 'completed',
    resource_id: '339710259795355',
    created_at: new Date().toISOString(),
    ...over,
  };
}

beforeEach(() => {
  notify.mockClear();
  transcribeMock.mockReset().mockResolvedValue({
    message: 'Transcription queued', resource_id: '339710259795355',
    points_charged: 5, transcription_pending_audio: false,
  });
  summaryMock.mockReset().mockResolvedValue({ message: 'Summary generation queued' });
  taskManagerMock.mockReset().mockReturnValue(null);
  resetTranscriptionFollowUps();
});

describe('useResourceProcessingFollowUps', () => {
  it('asks for the summary when the transcription this session started completes', async () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task()] });

    renderHook(() => useResourceProcessingFollowUps());

    expect(summaryMock).toHaveBeenCalledTimes(1);
    expect(summaryMock).toHaveBeenCalledWith('339710259795355');
    expect(transcriptionFollowUps()).toHaveLength(0);
  });

  it('matches resource_id across the number/string wire split', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task({ resource_id: 339710259795355 })] });

    renderHook(() => useResourceProcessingFollowUps());

    expect(summaryMock).toHaveBeenCalledWith('339710259795355');
  });

  it('does not fire twice when the task list re-renders', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task()] });

    const { rerender } = renderHook(() => useResourceProcessingFollowUps());
    taskManagerMock.mockReturnValue({ tasks: [task(), task({ id: 'wf-2' })] });
    rerender();

    expect(summaryMock).toHaveBeenCalledTimes(1);
  });

  it('waits while the transcription is still running', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task({ status: 'processing' })] });

    renderHook(() => useResourceProcessingFollowUps());

    expect(summaryMock).not.toHaveBeenCalled();
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('does not treat a finished audio extraction as a finished transcript', () => {
    // extract_audio chains INTO transcription; its completion means the
    // transcript work is starting, not that there is a transcript.
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({
      tasks: [task({ task_type: 'extract_audio', status: 'completed' })],
    });

    renderHook(() => useResourceProcessingFollowUps());

    expect(summaryMock).not.toHaveBeenCalled();
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('drops the follow-up when the transcription failed — no summary to make', () => {
    rememberTranscriptionFollowUp('339710259795355');
    // A real failed row carries `completed_at`: UnifiedTaskManager.fail()
    // writes it alongside the status (backend/app/services/infra/
    // unified_task_manager.py), as does mark_lost(). Omitting it here would
    // make this fixture the CANCEL shape instead — a different row and a
    // different verdict (see the "dead status with no terminal stamp" block).
    taskManagerMock.mockReturnValue({
      tasks: [task({ status: 'failed', completed_at: new Date().toISOString() })],
    });

    renderHook(() => useResourceProcessingFollowUps());

    expect(summaryMock).not.toHaveBeenCalled();
    expect(transcriptionFollowUps()).toHaveLength(0);
  });

  it('degrades silently with no TaskManagerProvider', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue(null);

    expect(() => renderHook(() => useResourceProcessingFollowUps())).not.toThrow();
    expect(summaryMock).not.toHaveBeenCalled();
    // Nothing was observed, so nothing may be concluded — the follow-up
    // stays queued for a host that does have the provider.
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('ignores tasks for other resources', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task({ resource_id: '111' })] });

    renderHook(() => useResourceProcessingFollowUps());

    expect(summaryMock).not.toHaveBeenCalled();
  });

  it('survives a rejected summary trigger without throwing into the render', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    summaryMock.mockRejectedValue(new Error('HTTP 500'));
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task()] });

    renderHook(() => useResourceProcessingFollowUps());
    await vi.waitFor(() => expect(errSpy).toHaveBeenCalled());
  });
});

/**
 * This hook is the third user-action→agent trigger path this task creates,
 * and it spends points exactly like the other two. "No news" would mean the
 * user is billed for a summary they were never told about, and told nothing
 * when it fails — the silent no-op the repo's discipline rules out.
 */
describe('useResourceProcessingFollowUps — user-visible outcome', () => {
  it('says the summary is being made', async () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task()] });

    renderHook(useFollowUpWithNotify);

    await vi.waitFor(() => expect(notify).toHaveBeenCalledWith(
      expect.stringMatching(/summar/i), 'info',
    ));
  });

  it('does not claim a charge when the backend deduped the summary', async () => {
    summaryMock.mockResolvedValue({
      message: 'Summary already in progress', resource_id: '339710259795355', points_charged: 0,
    });
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task()] });

    renderHook(useFollowUpWithNotify);

    await vi.waitFor(() => expect(notify).toHaveBeenCalledWith(
      expect.stringMatching(/already/i), 'info',
    ));
  });

  it('reports a failed summary trigger with its reason', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    summaryMock.mockRejectedValue(new Error('HTTP 402 Insufficient points'));
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task()] });

    renderHook(useFollowUpWithNotify);

    await vi.waitFor(() => expect(notify).toHaveBeenCalledWith(
      expect.stringContaining('Insufficient points'), 'error',
    ));
  });

  it('still works with no notifier (the hook is optional-notify)', async () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task()] });

    expect(() => renderHook(() => useResourceProcessingFollowUps())).not.toThrow();
    expect(summaryMock).toHaveBeenCalledTimes(1);
  });
});

/**
 * M1: the waiting list is armed the moment the transcription is triggered,
 * but the new task row only shows up in the Task Center a beat later. In
 * that window a STALE completed transcription for the same resource would
 * otherwise be read as "the one we just started has finished".
 */
describe('useResourceProcessingFollowUps — only tasks from this trigger onward', () => {
  it('ignores a transcription that completed before we asked for one', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({
      tasks: [task({ created_at: '2026-08-01T00:00:00Z' })],
    });

    renderHook(useFollowUpWithNotify);

    expect(summaryMock).not.toHaveBeenCalled();
    // Still waiting for the real one — an old row is not evidence either way.
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('accepts a task stamped slightly before the trigger (server clock skew)', () => {
    // task_tracking.created_at is the SERVER's clock; the waiting list is
    // stamped with the BROWSER's. A strict comparison would strand the
    // chain forever whenever the server runs a few seconds behind.
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({
      tasks: [task({ created_at: new Date(Date.now() - 5_000).toISOString() })],
    });

    renderHook(useFollowUpWithNotify);

    expect(summaryMock).toHaveBeenCalledWith('339710259795355');
  });
});

/**
 * The transcribe endpoint can refuse outright: an audio extraction with no
 * transcription intent holds migration 121's unique slot, so the 200 carries
 * `transcription_pending_audio: true`, queues nothing, charges nothing, and
 * says to retry once the blocker finishes (T1b contract §6.4).
 *
 * The user asked for a transcript, so the retry is ours to make — but only
 * when the blocker really finished, because the retry spends points.
 */
describe('useResourceProcessingFollowUps — blocked behind an audio extraction', () => {
  const BLOCKER = 'wf-audio-1';

  function blocker(over: Record<string, unknown> = {}) {
    return {
      id: BLOCKER,
      dbos_workflow_id: BLOCKER,
      task_type: 'extract_audio',
      status: 'completed',
      resource_id: '339710259795355',
      created_at: new Date().toISOString(),
      ...over,
    };
  }

  it('re-requests the transcription once the blocker completes', async () => {
    rememberPendingAudioRetry('339710259795355', BLOCKER);
    taskManagerMock.mockReturnValue({ tasks: [blocker()] });

    renderHook(useFollowUpWithNotify);

    expect(transcribeMock).toHaveBeenCalledWith('339710259795355');
    expect(pendingAudioRetries()).toHaveLength(0);
    // The retry really dispatched, so the summary half of the chain is armed.
    await vi.waitFor(() => expect(transcriptionFollowUps()).toContain('339710259795355'));
    await vi.waitFor(() => expect(notify).toHaveBeenCalledWith(
      expect.stringContaining('5'), 'info',
    ));
    // Dispatched, not adopted: this retry is what started the run, so the
    // watcher must judge it by the ordinary floor.
    expect(transcriptionFollowUp('339710259795355')?.adopted).toBe(false);
  });

  it('arms an ADOPTED wait when the retry lands on a run already going', () => {
    // The slot freed up and somebody else's transcription got there first,
    // so the retry is deduped: nothing dispatched, 0 points. The run it
    // returns for started BEFORE this moment, which is the whole reason the
    // wait has to know which shape it is — armed as "dispatched" here, its
    // completion falls outside the floor and the summary never comes.
    transcribeMock.mockResolvedValue({
      message: 'Transcription already in progress',
      resource_id: '339710259795355',
      points_charged: 0,
      transcription_pending_audio: false,
    });
    rememberPendingAudioRetry('339710259795355', BLOCKER);
    taskManagerMock.mockReturnValue({ tasks: [blocker()] });

    renderHook(useFollowUpWithNotify);

    return vi.waitFor(() =>
      expect(transcriptionFollowUp('339710259795355')?.adopted).toBe(true));
  });

  it('waits while the blocker is still running', () => {
    rememberPendingAudioRetry('339710259795355', BLOCKER);
    taskManagerMock.mockReturnValue({ tasks: [blocker({ status: 'processing' })] });

    renderHook(useFollowUpWithNotify);

    expect(transcribeMock).not.toHaveBeenCalled();
    expect(pendingAudioRetries()).toHaveLength(1);
  });

  it('does not spend points when the extraction failed — there is no audio', async () => {
    rememberPendingAudioRetry('339710259795355', BLOCKER);
    taskManagerMock.mockReturnValue({
      tasks: [blocker({ status: 'failed', error_msg: 'ffmpeg exited 1' })],
    });

    renderHook(useFollowUpWithNotify);

    expect(transcribeMock).not.toHaveBeenCalled();
    expect(pendingAudioRetries()).toHaveLength(0);
    await vi.waitFor(() => expect(notify).toHaveBeenCalledWith(
      expect.stringContaining('ffmpeg exited 1'), 'error',
    ));
  });

  it('treats a cancelled or lost extraction the same as a failed one', () => {
    rememberPendingAudioRetry('a', BLOCKER);
    taskManagerMock.mockReturnValue({ tasks: [blocker({ status: 'cancelled' })] });
    renderHook(useFollowUpWithNotify);
    expect(transcribeMock).not.toHaveBeenCalled();

    resetTranscriptionFollowUps();
    rememberPendingAudioRetry('b', BLOCKER);
    taskManagerMock.mockReturnValue({ tasks: [blocker({ status: 'lost' })] });
    renderHook(useFollowUpWithNotify);
    expect(transcribeMock).not.toHaveBeenCalled();
  });

  it('retries straight away when the blocker had already finished', () => {
    // blocking_task_id is null: the winner ended before the response was
    // written, so there is no task to observe — waiting would be waiting
    // forever.
    rememberPendingAudioRetry('339710259795355', null);
    taskManagerMock.mockReturnValue({ tasks: [] });

    renderHook(useFollowUpWithNotify);

    expect(transcribeMock).toHaveBeenCalledWith('339710259795355');
  });

  it('retries a null-blocker entry even with no TaskManagerProvider', () => {
    rememberPendingAudioRetry('339710259795355', null);
    taskManagerMock.mockReturnValue(null);

    renderHook(useFollowUpWithNotify);

    expect(transcribeMock).toHaveBeenCalledWith('339710259795355');
  });

  it('never retries blind when there IS a blocker to observe', () => {
    rememberPendingAudioRetry('339710259795355', BLOCKER);
    taskManagerMock.mockReturnValue(null);

    renderHook(useFollowUpWithNotify);

    expect(transcribeMock).not.toHaveBeenCalled();
    expect(pendingAudioRetries()).toHaveLength(1);
  });

  it('does not loop when the retry is blocked again', async () => {
    transcribeMock.mockResolvedValue({
      message: 'Audio extraction is already running…',
      resource_id: '339710259795355',
      points_charged: 0,
      transcription_pending_audio: true,
      blocking_task_id: 'wf-audio-2',
    });
    rememberPendingAudioRetry('339710259795355', BLOCKER);
    taskManagerMock.mockReturnValue({ tasks: [blocker()] });

    const { rerender } = renderHook(useFollowUpWithNotify);

    // Wait for the response to be PROCESSED before asserting the waiting
    // list. Asserting it earlier passes either way: the entry is dropped
    // before the request is sent, so "empty" is transiently true even for
    // an implementation that re-arms afterwards.
    await vi.waitFor(() => expect(notify).toHaveBeenCalledWith(
      expect.stringMatching(/again|retry/i), 'info',
    ));

    // Re-arming here is how one automatic retry becomes an unbounded chain
    // of them; the user is told instead.
    expect(pendingAudioRetries()).toHaveLength(0);
    taskManagerMock.mockReturnValue({ tasks: [blocker(), blocker({ id: 'wf-x' })] });
    rerender();
    expect(transcribeMock).toHaveBeenCalledTimes(1);
  });

  it('fires the retry once even as the task list re-renders', () => {
    rememberPendingAudioRetry('339710259795355', BLOCKER);
    taskManagerMock.mockReturnValue({ tasks: [blocker()] });

    const { rerender } = renderHook(useFollowUpWithNotify);
    taskManagerMock.mockReturnValue({ tasks: [blocker(), blocker({ id: 'wf-other' })] });
    rerender();

    expect(transcribeMock).toHaveBeenCalledTimes(1);
  });
});

/**
 * The production shape this file previously had no case for: the user
 * attached a video whose transcription was ALREADY running, so the trigger
 * deduped ("already in progress", 0 points) and nothing new was dispatched.
 * The run therefore started BEFORE the wait was registered — which is
 * exactly what the staleness floor above rejects. Result in production: the
 * transcript landed a minute later and the resource ended with zero summary
 * rows, because the completion was read as somebody else's work.
 */
describe('useResourceProcessingFollowUps — a run we attached to, not started', () => {
  /** The in-flight run: created minutes ago, finishing now. */
  const adoptedTask = (over: Record<string, unknown> = {}) =>
    task({
      created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
      completed_at: new Date().toISOString(),
      ...over,
    });

  it('summarises when the transcription it attached to completes', () => {
    rememberTranscriptionFollowUp('339710259795355', { adopted: true });
    taskManagerMock.mockReturnValue({ tasks: [adoptedTask()] });

    renderHook(useFollowUpWithNotify);

    expect(summaryMock).toHaveBeenCalledTimes(1);
    expect(summaryMock).toHaveBeenCalledWith('339710259795355');
    expect(transcriptionFollowUps()).toHaveLength(0);
  });

  /**
   * Production rarely shows ONE row: a resource being transcribed again
   * usually still carries the previous run's completed row, so the list the
   * hook reads has both. `latestTaskFor` picks by `created_at` descending
   * and the run in flight is the newer one — these two pin that selection,
   * because a single-row fixture cannot tell a correct pick from a lucky
   * one.
   */
  /** The previous run: finished long before any of this. */
  const previousRun = () =>
    task({
      id: 'wf-previous',
      created_at: new Date(Date.now() - 30 * 60_000).toISOString(),
      completed_at: new Date(Date.now() - 25 * 60_000).toISOString(),
    });

  it('keeps waiting while the newer run is going, old completed row present', () => {
    rememberTranscriptionFollowUp('339710259795355', { adopted: true });
    taskManagerMock.mockReturnValue({
      tasks: [previousRun(), adoptedTask({ status: 'in_progress', completed_at: null })],
    });

    renderHook(useFollowUpWithNotify);

    // Reading the older row here would summarise a transcript the run in
    // flight is about to replace.
    expect(summaryMock).not.toHaveBeenCalled();
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('summarises once the newer run completes, old completed row present', () => {
    rememberTranscriptionFollowUp('339710259795355', { adopted: true });
    taskManagerMock.mockReturnValue({ tasks: [previousRun(), adoptedTask()] });

    renderHook(useFollowUpWithNotify);

    expect(summaryMock).toHaveBeenCalledTimes(1);
    expect(summaryMock).toHaveBeenCalledWith('339710259795355');
  });

  it('still waits while that run is in progress', () => {
    rememberTranscriptionFollowUp('339710259795355', { adopted: true });
    taskManagerMock.mockReturnValue({
      tasks: [adoptedTask({ status: 'in_progress', completed_at: null })],
    });

    renderHook(useFollowUpWithNotify);

    expect(summaryMock).not.toHaveBeenCalled();
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('ignores a run that had already finished before we attached', () => {
    // Not the run the backend told us about — that one is still going and
    // its transcript will overwrite this one's. Summarising here spends the
    // user's points on output that is about to be stale.
    rememberTranscriptionFollowUp('339710259795355', { adopted: true });
    taskManagerMock.mockReturnValue({
      tasks: [adoptedTask({ completed_at: new Date(Date.now() - 4 * 60_000).toISOString() })],
    });

    renderHook(useFollowUpWithNotify);

    expect(summaryMock).not.toHaveBeenCalled();
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('falls back to updated_at when the row reached us without completed_at', () => {
    rememberTranscriptionFollowUp('339710259795355', { adopted: true });
    taskManagerMock.mockReturnValue({
      tasks: [adoptedTask({ completed_at: null, updated_at: new Date().toISOString() })],
    });

    renderHook(useFollowUpWithNotify);

    expect(summaryMock).toHaveBeenCalledWith('339710259795355');
  });

  it('does not relax the floor for a run this session dispatched', () => {
    // Same task row, dispatched entry: here an older run really is somebody
    // else's, and the one we started has yet to appear in the Task Center.
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [adoptedTask()] });

    renderHook(useFollowUpWithNotify);

    expect(summaryMock).not.toHaveBeenCalled();
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('summarises once even as the task list re-renders', () => {
    rememberTranscriptionFollowUp('339710259795355', { adopted: true });
    taskManagerMock.mockReturnValue({ tasks: [adoptedTask()] });

    const { rerender } = renderHook(useFollowUpWithNotify);
    taskManagerMock.mockReturnValue({ tasks: [adoptedTask(), adoptedTask({ id: 'wf-2' })] });
    rerender();

    expect(summaryMock).toHaveBeenCalledTimes(1);
  });
});

/**
 * PROBE-D: a dead status is not always the end of the run.
 *
 * `UnifiedTaskManager.cancel()` writes `phase`/`status` DIRECTLY and stamps NO
 * `completed_at`, and it does not stop the DBOS workflow — it kills registered
 * subprocesses best-effort and leaves cancelling the workflow itself as a TODO
 * (backend/app/services/infra/unified_task_manager.py). So `status:'cancelled'`
 * with a null `completed_at` sits in front of a run that may still be going and
 * may still flip to `completed`.
 *
 * The adopted shape already accepts `updated_at` as a stand-in for
 * `completed_at`, and that fallback matched such a row: the wait was dropped
 * while its transcription was still in flight, and the summary the user paid a
 * trigger for never came — silently, with nothing left to re-check. Hence the
 * asymmetry these cases pin: the fallback may ADMIT a task (worst case, a
 * summary request the backend dedupes) but may not be the evidence that
 * FORGETS one.
 */
describe('useResourceProcessingFollowUps — dead status with no terminal stamp', () => {
  it('keeps waiting on a cancel that only flipped the status (adopted)', () => {
    rememberTranscriptionFollowUp('339710259795355', { adopted: true });
    taskManagerMock.mockReturnValue({
      tasks: [
        task({
          status: 'cancelled',
          created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
          completed_at: null,
          // Moves on ANY write, the status-only flip included — which is why
          // it cannot stand in for the mirror trigger's terminal stamp here.
          updated_at: new Date().toISOString(),
        }),
      ],
    });

    renderHook(useFollowUpWithNotify);

    expect(summaryMock).not.toHaveBeenCalled();
    // The run behind that row may still finish; dropping the entry now is the
    // silent discard this case exists to prevent.
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('keeps waiting on the same shape for a run this session dispatched', () => {
    // cancel() stamps nothing either way, so the dispatched shape needs the
    // same guard — its row simply matches on `created_at` instead.
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({
      tasks: [task({ status: 'cancelled', completed_at: null, updated_at: new Date().toISOString() })],
    });

    renderHook(useFollowUpWithNotify);

    expect(summaryMock).not.toHaveBeenCalled();
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('summarises if that cancelled run turns out to have completed after all', () => {
    // The point of keeping the entry: the mirror trigger later reports the
    // workflow's real outcome and the chain finishes as the user asked.
    rememberTranscriptionFollowUp('339710259795355', { adopted: true });
    const stale = {
      status: 'cancelled',
      created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
      completed_at: null,
      updated_at: new Date().toISOString(),
    };
    taskManagerMock.mockReturnValue({ tasks: [task(stale)] });
    const { rerender } = renderHook(useFollowUpWithNotify);
    expect(summaryMock).not.toHaveBeenCalled();

    taskManagerMock.mockReturnValue({
      tasks: [task({ ...stale, status: 'completed', completed_at: new Date().toISOString() })],
    });
    rerender();

    expect(summaryMock).toHaveBeenCalledWith('339710259795355');
    expect(transcriptionFollowUps()).toHaveLength(0);
  });

  it('still drops the wait on a stamped cancel / failure / loss', () => {
    // The other half of the rule: `completed_at` present means the workflow
    // really ended, so waiting on is pointless. Without these, "keep waiting
    // on a dead status" could be implemented as "never forget at all".
    for (const status of ['cancelled', 'failed', 'lost']) {
      resetTranscriptionFollowUps();
      rememberTranscriptionFollowUp('339710259795355', { adopted: true });
      taskManagerMock.mockReturnValue({
        tasks: [
          task({
            status,
            created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
            completed_at: new Date().toISOString(),
          }),
        ],
      });

      const { unmount } = renderHook(useFollowUpWithNotify);

      expect(summaryMock).not.toHaveBeenCalled();
      expect(transcriptionFollowUps()).toHaveLength(0);
      unmount();
    }
  });
});
