/**
 * Unit tests for ensureResourceProcessed — the "top up whatever is
 * missing" helper shared by the @ picker and the Send to Agent menu.
 *
 * The two trigger endpoints are idempotent server-side (in-flight dedup
 * returns 200 "already in progress"), so the helper never needs to guard
 * against double clicks; what it MUST guarantee is that a trigger failure
 * comes back as a typed result instead of throwing into the chat flow.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const transcribeMock = vi.fn();
const summaryMock = vi.fn();

vi.mock('../services/aiService', () => ({
  triggerTranscriptionByResource: (id: string) => transcribeMock(id),
  triggerSummaryByResource: (id: string) => summaryMock(id),
}));

import { ensureResourceProcessed } from './ensureResourceProcessed';

/** Real wire body of POST /api/v1/ai/transcribe/resource/{id} — no task_id. */
const TRANSCRIBE_OK = {
  message: 'Transcription queued',
  resource_id: 'r-1',
  platform_id: 'p-1',
  points_charged: 5,
  extracting_audio: false,
};
/** Real wire body of POST /api/v1/ai/summarize/resource/{id}. */
const SUMMARY_OK = {
  message: 'Summary generation queued',
  resource_id: 'r-1',
  platform_id: 'p-1',
};

beforeEach(() => {
  vi.restoreAllMocks();
  transcribeMock.mockReset().mockResolvedValue(TRANSCRIBE_OK);
  summaryMock.mockReset().mockResolvedValue(SUMMARY_OK);
});

describe('ensureResourceProcessed', () => {
  it('triggers transcription when a video has no transcript', async () => {
    const result = await ensureResourceProcessed({
      id: 'r-1',
      kind: 'video',
      transcript_status: 'none',
      summary_status: 'none',
    });

    expect(result.action).toBe('triggered_transcribe');
    expect(transcribeMock).toHaveBeenCalledTimes(1);
    expect(transcribeMock).toHaveBeenCalledWith('r-1');
    expect(summaryMock).not.toHaveBeenCalled();
  });

  it('re-triggers transcription after a failed run', async () => {
    const result = await ensureResourceProcessed({
      id: 'r-1',
      kind: 'video',
      transcript_status: 'failed',
    });

    expect(result.action).toBe('triggered_transcribe');
    expect(transcribeMock).toHaveBeenCalledWith('r-1');
  });

  it('triggers summary once the transcript is complete', async () => {
    const result = await ensureResourceProcessed({
      id: 'r-2',
      kind: 'audio',
      transcript_status: 'completed',
      summary_status: 'none',
    });

    expect(result.action).toBe('triggered_summary');
    expect(summaryMock).toHaveBeenCalledTimes(1);
    expect(summaryMock).toHaveBeenCalledWith('r-2');
    expect(transcribeMock).not.toHaveBeenCalled();
  });

  it('triggers nothing when transcript and summary are both complete', async () => {
    const result = await ensureResourceProcessed({
      id: 'r-3',
      kind: 'video',
      transcript_status: 'completed',
      summary_status: 'completed',
    });

    expect(result.action).toBe('ready');
    expect(transcribeMock).not.toHaveBeenCalled();
    expect(summaryMock).not.toHaveBeenCalled();
  });

  it('skips non-audiovisual resources', async () => {
    const image = await ensureResourceProcessed({
      id: 'r-4',
      kind: 'image',
      mime: 'image/png',
    });
    const doc = await ensureResourceProcessed({ id: 'r-5', kind: 'doc' });

    expect(image.action).toBe('skipped');
    expect(doc.action).toBe('skipped');
    expect(transcribeMock).not.toHaveBeenCalled();
    expect(summaryMock).not.toHaveBeenCalled();
  });

  it('falls back to the mime type when kind is absent', async () => {
    const result = await ensureResourceProcessed({
      id: 'r-6',
      mime: 'audio/mpeg',
      transcript_status: 'none',
    });

    expect(result.action).toBe('triggered_transcribe');
    expect(transcribeMock).toHaveBeenCalledWith('r-6');
  });

  it('treats a missing transcript_status as "not transcribed yet"', async () => {
    const result = await ensureResourceProcessed({ id: 'r-7', kind: 'video' });

    expect(result.action).toBe('triggered_transcribe');
  });

  it('does not trigger when the backend marked the step skipped', async () => {
    const noAudio = await ensureResourceProcessed({
      id: 'r-8',
      kind: 'video',
      transcript_status: 'skipped',
    });
    const noSummary = await ensureResourceProcessed({
      id: 'r-9',
      kind: 'video',
      transcript_status: 'completed',
      summary_status: 'skipped',
    });

    expect(noAudio.action).toBe('skipped');
    expect(noSummary.action).toBe('skipped');
    expect(transcribeMock).not.toHaveBeenCalled();
    expect(summaryMock).not.toHaveBeenCalled();
  });

  it('returns a typed failure instead of throwing when the trigger fails', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    transcribeMock.mockRejectedValue(new Error('HTTP 402 Insufficient points'));

    const result = await ensureResourceProcessed({
      id: 'r-10',
      kind: 'video',
      transcript_status: 'none',
    });

    expect(result.action).toBe('failed');
    expect(result.attempted).toBe('transcribe');
    expect(result.error).toContain('Insufficient points');
    expect(errSpy).toHaveBeenCalled();
  });

  it('reports which step failed when the summary trigger rejects', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    summaryMock.mockRejectedValue(new Error('HTTP 500'));

    const result = await ensureResourceProcessed({
      id: 'r-11',
      kind: 'video',
      transcript_status: 'completed',
      summary_status: 'none',
    });

    expect(result.action).toBe('failed');
    expect(result.attempted).toBe('summary');
    expect(result.error).toContain('HTTP 500');
  });

  it('is safe to call repeatedly — the endpoints dedup server-side', async () => {
    transcribeMock.mockResolvedValue({
      message: 'Transcription already in progress',
      resource_id: 'r-12',
    });

    const first = await ensureResourceProcessed({
      id: 'r-12',
      kind: 'video',
      transcript_status: 'processing',
    });
    const second = await ensureResourceProcessed({
      id: 'r-12',
      kind: 'video',
      transcript_status: 'processing',
    });

    expect(first.action).toBe('triggered_transcribe');
    expect(second.action).toBe('triggered_transcribe');
    expect(transcribeMock).toHaveBeenCalledTimes(2);
  });
});
