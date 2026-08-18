/**
 * useTranscriptionSummaryFollowUp — the second half of F1's chain.
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
vi.mock('../services/aiService', () => ({
  triggerSummaryByResource: (id: string) => summaryMock(id),
}));

const taskManagerMock = vi.fn();
vi.mock('./useOptionalTaskManager', () => ({
  useOptionalTaskManager: () => taskManagerMock(),
}));

import { useTranscriptionSummaryFollowUp } from './useTranscriptionSummaryFollowUp';
import {
  rememberTranscriptionFollowUp,
  resetTranscriptionFollowUps,
  transcriptionFollowUps,
} from '../utils/transcriptionFollowUp';

/** Real task_tracking wire shape: resource_id is a STRING column. */
function task(over: Record<string, unknown> = {}) {
  return {
    id: 'wf-1',
    task_type: 'ai_transcription',
    status: 'completed',
    resource_id: '339710259795355',
    created_at: '2026-08-17T10:00:00Z',
    ...over,
  };
}

beforeEach(() => {
  summaryMock.mockReset().mockResolvedValue({ message: 'Summary generation queued' });
  taskManagerMock.mockReset().mockReturnValue(null);
  resetTranscriptionFollowUps();
});

describe('useTranscriptionSummaryFollowUp', () => {
  it('asks for the summary when the transcription this session started completes', async () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task()] });

    renderHook(() => useTranscriptionSummaryFollowUp());

    expect(summaryMock).toHaveBeenCalledTimes(1);
    expect(summaryMock).toHaveBeenCalledWith('339710259795355');
    expect(transcriptionFollowUps()).toHaveLength(0);
  });

  it('matches resource_id across the number/string wire split', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task({ resource_id: 339710259795355 })] });

    renderHook(() => useTranscriptionSummaryFollowUp());

    expect(summaryMock).toHaveBeenCalledWith('339710259795355');
  });

  it('does not fire twice when the task list re-renders', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task()] });

    const { rerender } = renderHook(() => useTranscriptionSummaryFollowUp());
    taskManagerMock.mockReturnValue({ tasks: [task(), task({ id: 'wf-2' })] });
    rerender();

    expect(summaryMock).toHaveBeenCalledTimes(1);
  });

  it('waits while the transcription is still running', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task({ status: 'processing' })] });

    renderHook(() => useTranscriptionSummaryFollowUp());

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

    renderHook(() => useTranscriptionSummaryFollowUp());

    expect(summaryMock).not.toHaveBeenCalled();
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('drops the follow-up when the transcription failed — no summary to make', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task({ status: 'failed' })] });

    renderHook(() => useTranscriptionSummaryFollowUp());

    expect(summaryMock).not.toHaveBeenCalled();
    expect(transcriptionFollowUps()).toHaveLength(0);
  });

  it('degrades silently with no TaskManagerProvider', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue(null);

    expect(() => renderHook(() => useTranscriptionSummaryFollowUp())).not.toThrow();
    expect(summaryMock).not.toHaveBeenCalled();
    // Nothing was observed, so nothing may be concluded — the follow-up
    // stays queued for a host that does have the provider.
    expect(transcriptionFollowUps()).toContain('339710259795355');
  });

  it('ignores tasks for other resources', () => {
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task({ resource_id: '111' })] });

    renderHook(() => useTranscriptionSummaryFollowUp());

    expect(summaryMock).not.toHaveBeenCalled();
  });

  it('survives a rejected summary trigger without throwing into the render', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    summaryMock.mockRejectedValue(new Error('HTTP 500'));
    rememberTranscriptionFollowUp('339710259795355');
    taskManagerMock.mockReturnValue({ tasks: [task()] });

    renderHook(() => useTranscriptionSummaryFollowUp());
    await vi.waitFor(() => expect(errSpy).toHaveBeenCalled());
  });
});
