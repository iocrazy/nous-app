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
  triggerTranscriptionByResource: (id: string, opts?: unknown) =>
    transcribeMock(id, opts),
  triggerSummaryByResource: (id: string) => summaryMock(id),
}));

const fetchResourceByIdMock = vi.fn();
vi.mock('../services/resourceService', () => ({
  fetchResourceById: (id: string) => fetchResourceByIdMock(id),
}));

import { ensureResourceProcessed } from './ensureResourceProcessed';
import {
  pendingAudioRetries,
  resetTranscriptionFollowUps,
  transcriptionFollowUp,
  transcriptionFollowUps,
} from './transcriptionFollowUp';

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
  // Default: the lookup is not expected; a test that wants it says so.
  fetchResourceByIdMock.mockReset().mockRejectedValue(new Error('unexpected lookup'));
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
    expect(transcribeMock).toHaveBeenCalledWith('r-1', {
      followUpSummary: true,
    });
    expect(summaryMock).not.toHaveBeenCalled();
  });

  it('re-triggers transcription after a failed run', async () => {
    const result = await ensureResourceProcessed({
      id: 'r-1',
      kind: 'video',
      transcript_status: 'failed',
    });

    expect(result.action).toBe('triggered_transcribe');
    expect(transcribeMock).toHaveBeenCalledWith('r-1', {
      followUpSummary: true,
    });
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
    expect(transcribeMock).toHaveBeenCalledWith('r-6', {
      followUpSummary: true,
    });
  });

  it('never guesses from a missing transcript_status — it looks it up', async () => {
    // "Nobody told me" is not "there is none". Guessing 'none' here bills a
    // second transcription for an asset that already has one, because the
    // trigger endpoint dedups in-flight work and nothing else.
    vi.spyOn(console, 'error').mockImplementation(() => {});
    fetchResourceByIdMock.mockResolvedValue({
      id: 'r-7', transcript_status: 'completed', summary_status: 'completed',
    });

    const result = await ensureResourceProcessed({ id: 'r-7', kind: 'video' });

    expect(fetchResourceByIdMock).toHaveBeenCalledWith('r-7');
    expect(result.action).toBe('ready');
    expect(transcribeMock).not.toHaveBeenCalled();
  });

  it('acts on the looked-up status when there really is work', async () => {
    fetchResourceByIdMock.mockResolvedValue({
      id: 'r-7b', transcript_status: 'none', summary_status: 'none',
    });

    const result = await ensureResourceProcessed({ id: 'r-7b', kind: 'video' });

    expect(result.action).toBe('triggered_transcribe');
    expect(transcribeMock).toHaveBeenCalledWith('r-7b', {
      followUpSummary: true,
    });
  });

  it('looks up an unknown SUMMARY on a transcribed asset too', async () => {
    // The summary endpoint charges as well, so the same guess is the same
    // bug one step down the ladder.
    fetchResourceByIdMock.mockResolvedValue({
      id: 'r-7c', transcript_status: 'completed', summary_status: 'completed',
    });

    const result = await ensureResourceProcessed({
      id: 'r-7c', kind: 'video', transcript_status: 'completed',
    });

    expect(fetchResourceByIdMock).toHaveBeenCalledWith('r-7c');
    expect(summaryMock).not.toHaveBeenCalled();
    expect(result.action).toBe('ready');
  });

  it('does no lookup when the caller already supplied the status', async () => {
    await ensureResourceProcessed({
      id: 'r-7d', kind: 'video', transcript_status: 'none', summary_status: 'none',
    });

    expect(fetchResourceByIdMock).not.toHaveBeenCalled();
  });

  it('treats an empty-string status as unknown, not as "none"', async () => {
    fetchResourceByIdMock.mockResolvedValue({
      id: 'r-7e', transcript_status: 'completed', summary_status: 'completed',
    });

    const result = await ensureResourceProcessed({
      id: 'r-7e', kind: 'video', transcript_status: '', summary_status: '',
    });

    expect(result.action).toBe('ready');
    expect(transcribeMock).not.toHaveBeenCalled();
  });

  it('does nothing when the status cannot be resolved at all', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    fetchResourceByIdMock.mockRejectedValue(new Error('HTTP 404'));

    const result = await ensureResourceProcessed({ id: 'r-7f', kind: 'video' });

    // Failing towards "did less" is the deliberate trade: the other
    // direction spends the user's points on work already done.
    expect(result.action).toBe('status_unknown');
    expect(result.error).toContain('404');
    expect(transcribeMock).not.toHaveBeenCalled();
  });

  it('does nothing when the lookup answers without the status columns', async () => {
    fetchResourceByIdMock.mockResolvedValue({ id: 'r-7g', filename: 'clip.mp4' });

    const result = await ensureResourceProcessed({ id: 'r-7g', kind: 'video' });

    expect(result.action).toBe('status_unknown');
    expect(transcribeMock).not.toHaveBeenCalled();
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

/**
 * The response body is the ONLY way the caller can tell "I just queued a
 * paid transcription" from "the backend deduped an in-flight one and
 * charged nothing" — both are 200s. Transcribe carries `points_charged`
 * (0 on the dedup path since Task 1b); summary's dedup arm carries only
 * the message, so both signals have to be read.
 */
describe('ensureResourceProcessed — response passthrough', () => {
  it('reports the points a fresh transcription charged', async () => {
    const result = await ensureResourceProcessed({
      id: 'r-20',
      kind: 'video',
      transcript_status: 'none',
    });

    expect(result.action).toBe('triggered_transcribe');
    expect(result.pointsCharged).toBe(5);
    expect(result.alreadyInProgress).toBe(false);
    expect(result.message).toBe('Transcription queued');
  });

  it('flags the deduped transcription as already running, not as a new charge', async () => {
    transcribeMock.mockResolvedValue({
      message: 'Transcription already in progress',
      resource_id: 'r-21',
      points_charged: 0,
    });

    const result = await ensureResourceProcessed({
      id: 'r-21',
      kind: 'video',
      transcript_status: 'processing',
    });

    expect(result.action).toBe('triggered_transcribe');
    expect(result.alreadyInProgress).toBe(true);
    expect(result.pointsCharged).toBe(0);
  });

  it('flags a deduped summary — same shape as transcribe since the conflict-capture fix', async () => {
    summaryMock.mockResolvedValue({
      message: 'Summary already in progress',
      resource_id: 'r-22',
      points_charged: 0,
    });

    const result = await ensureResourceProcessed({
      id: 'r-22',
      kind: 'video',
      transcript_status: 'completed',
      summary_status: 'none',
    });

    expect(result.action).toBe('triggered_summary');
    expect(result.alreadyInProgress).toBe(true);
    expect(result.pointsCharged).toBe(0);
  });

  it('falls back to the message when an arm reports no cost field at all', async () => {
    // The fresh-summary arm genuinely omits `points_charged`, so an omitted
    // field cannot mean "deduped" on its own — this is the belt for an arm
    // that dedups without saying so numerically.
    summaryMock.mockResolvedValue({ message: 'Summary already in progress', resource_id: 'r-23' });

    const result = await ensureResourceProcessed({
      id: 'r-23', kind: 'video', transcript_status: 'completed', summary_status: 'none',
    });

    expect(result.alreadyInProgress).toBe(true);
  });

  it('does not read a freshly queued summary as deduped', async () => {
    const result = await ensureResourceProcessed({
      id: 'r-24', kind: 'video', transcript_status: 'completed', summary_status: 'none',
    });

    expect(result.action).toBe('triggered_summary');
    expect(result.alreadyInProgress).toBe(false);
  });
});

/**
 * F1's "chain whatever is missing" only completes if something notices the
 * transcript landing and asks for the summary. The helper is the single
 * place that knows a transcription was just started, so it is the place
 * that records the follow-up — a caller cannot forget to.
 */
describe('ensureResourceProcessed — transcript → summary follow-up', () => {
  beforeEach(() => {
    resetTranscriptionFollowUps();
  });

  it('records a follow-up when it starts a transcription', async () => {
    await ensureResourceProcessed({ id: 'r-30', kind: 'video', transcript_status: 'none' });

    expect(transcriptionFollowUps()).toContain('r-30');
  });

  it('records nothing when no transcription was started', async () => {
    await ensureResourceProcessed({
      id: 'r-31', kind: 'video', transcript_status: 'completed', summary_status: 'none',
    });
    await ensureResourceProcessed({
      id: 'r-32', kind: 'video', transcript_status: 'completed', summary_status: 'completed',
    });
    await ensureResourceProcessed({ id: 'r-33', kind: 'image', mime: 'image/png' });

    expect(transcriptionFollowUps()).toHaveLength(0);
  });

  it('records a follow-up when the transcription was ALREADY running', async () => {
    // The dedup arm: the user attached a video whose transcription somebody
    // (or an earlier click) already started. Nothing was queued and nothing
    // was charged — but the summary is still owed, and without this entry
    // the transcript lands and the chain stops there. Observed in
    // production as a resource with a transcript and zero summary rows.
    transcribeMock.mockResolvedValue({
      message: 'Transcription already in progress',
      resource_id: 'r-35',
      points_charged: 0,
    });

    const result = await ensureResourceProcessed({
      id: 'r-35', kind: 'video', transcript_status: 'processing',
    });

    expect(result.alreadyInProgress).toBe(true);
    expect(transcriptionFollowUps()).toContain('r-35');
    // Marked as adopted: the run it waits on started BEFORE this call, so
    // the watcher must not judge it by when the wait was registered.
    expect(transcriptionFollowUp('r-35')?.adopted).toBe(true);
  });

  it('marks a freshly dispatched transcription as not adopted', async () => {
    await ensureResourceProcessed({ id: 'r-36', kind: 'video', transcript_status: 'none' });

    expect(transcriptionFollowUp('r-36')?.adopted).toBe(false);
  });

  it('records nothing when the transcription trigger failed', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    transcribeMock.mockRejectedValue(new Error('HTTP 402'));

    await ensureResourceProcessed({ id: 'r-34', kind: 'video', transcript_status: 'none' });

    expect(transcriptionFollowUps()).toHaveLength(0);
  });
});

/**
 * The transcribe endpoint has a 200 arm that queues NOTHING: an audio
 * extraction started with no transcription intent holds migration 121's
 * unique slot, so the call is refused politely and the user must retry.
 * It looks like a dedup (`points_charged: 0`) and is not one — reading it
 * as "already being processed" strands the user waiting for work nobody
 * started, which is the lie the backend fix removed.
 */
describe('ensureResourceProcessed — blocked behind an audio extraction', () => {
  it('does not report a no-op dispatch as work in progress', async () => {
    transcribeMock.mockResolvedValue({
      message: 'Audio extraction is already running for this media, and that run '
        + 'will not start a transcription by itself — retry once it finishes',
      resource_id: 'r-40',
      platform_id: 'p-40',
      points_charged: 0,
      transcription_pending_audio: true,
      blocking_task_id: 'wf-audio-1',
    });

    const result = await ensureResourceProcessed({
      id: 'r-40', kind: 'video', transcript_status: 'none', summary_status: 'none',
    });

    expect(result.action).toBe('pending_audio');
    expect(result.alreadyInProgress).toBeFalsy();
    expect(result.blockingTaskId).toBe('wf-audio-1');
  });

  it('queues a retry against the task that is holding the slot', async () => {
    resetTranscriptionFollowUps();
    transcribeMock.mockResolvedValue({
      message: 'Audio extraction is already running…',
      resource_id: 'r-42',
      points_charged: 0,
      transcription_pending_audio: true,
      blocking_task_id: 'wf-audio-9',
    });

    await ensureResourceProcessed({
      id: 'r-42', kind: 'video', transcript_status: 'none', summary_status: 'none',
    });

    expect(pendingAudioRetries()).toEqual([
      expect.objectContaining({ resourceId: 'r-42', blockingTaskId: 'wf-audio-9' }),
    ]);
  });

  it('queues a retry with no blocker when the winner already finished', async () => {
    resetTranscriptionFollowUps();
    transcribeMock.mockResolvedValue({
      message: 'Audio extraction is already running…',
      resource_id: 'r-43',
      points_charged: 0,
      transcription_pending_audio: true,
      blocking_task_id: null,
    });

    const result = await ensureResourceProcessed({
      id: 'r-43', kind: 'video', transcript_status: 'none', summary_status: 'none',
    });

    expect(result.blockingTaskId).toBeNull();
    expect(pendingAudioRetries()).toEqual([
      expect.objectContaining({ resourceId: 'r-43', blockingTaskId: null }),
    ]);
  });

  it('does not wait for a transcript that was never started', async () => {
    resetTranscriptionFollowUps();
    transcribeMock.mockResolvedValue({
      message: 'Audio extraction is already running…',
      resource_id: 'r-41',
      points_charged: 0,
      transcription_pending_audio: true,
    });

    await ensureResourceProcessed({
      id: 'r-41', kind: 'video', transcript_status: 'none', summary_status: 'none',
    });

    expect(transcriptionFollowUps()).not.toContain('r-41');
  });
});

/**
 * The production incident of 2026-08-20, from the frontend side.
 *
 * The row this helper decides from is a snapshot taken before the resource
 * finished transcribing, so it says `transcript_status: 'none'` about an
 * asset that has a transcript. The old code read that at face value and
 * asked for a second, paid transcription; the endpoint's in-flight dedup
 * could not stop it, because by then nothing was in flight.
 *
 * The backend now answers `already_transcribed` instead of dispatching, and
 * the contract this file pins is what the helper does WITH that answer: the
 * transcription is not re-reported as started, no follow-up watcher is left
 * waiting on a task nobody created, and the chain moves on to the summary —
 * which is what the user attached the asset for.
 */
describe('ensureResourceProcessed — stale snapshot, finished work', () => {
  /** Real wire body of the transcribe endpoint's short-circuit arm. */
  const TRANSCRIBE_ALREADY = {
    message: 'Transcript already exists',
    resource_id: 'r-1',
    platform_id: 'p-1',
    points_charged: 0,
    transcription_pending_audio: false,
    already_transcribed: true,
  };
  /** Real wire body of the summarize endpoint's short-circuit arm. */
  const SUMMARY_ALREADY = {
    message: 'Summary already exists',
    resource_id: 'r-1',
    platform_id: 'p-1',
    points_charged: 0,
    already_summarized: true,
  };

  it('continues to the summary instead of reporting a transcription', async () => {
    resetTranscriptionFollowUps();
    transcribeMock.mockResolvedValue(TRANSCRIBE_ALREADY);

    const result = await ensureResourceProcessed({
      id: 'r-50', kind: 'video', transcript_status: 'none', summary_status: 'none',
    });

    expect(summaryMock).toHaveBeenCalledWith('r-50');
    expect(result.action).toBe('triggered_summary');
    expect(result.alreadyTranscribed).toBe(true);
    // Nothing is running, so nothing may be waited on: a watcher registered
    // here would poll for a task that was never created.
    expect(transcriptionFollowUps()).not.toContain('r-50');
  });

  it('does not present a short-circuit as an in-flight dedup', async () => {
    // `points_charged: 0` makes isDedupedResponse true, so the two answers
    // are indistinguishable by cost alone — and they mean opposite things
    // to the user ("wait for the run" vs "it is already there").
    transcribeMock.mockResolvedValue(TRANSCRIBE_ALREADY);

    const result = await ensureResourceProcessed({
      id: 'r-51', kind: 'video', transcript_status: 'none', summary_status: 'none',
    });

    expect(result.action).not.toBe('triggered_transcribe');
    expect(result.alreadyInProgress).toBeFalsy();
  });

  it('ends ready when the summary turns out to exist too', async () => {
    transcribeMock.mockResolvedValue(TRANSCRIBE_ALREADY);
    summaryMock.mockResolvedValue(SUMMARY_ALREADY);

    const result = await ensureResourceProcessed({
      id: 'r-52', kind: 'video', transcript_status: 'none', summary_status: 'none',
    });

    expect(result.action).toBe('ready');
    expect(result.pointsCharged).toBe(0);
    expect(result.alreadyTranscribed).toBe(true);
  });

  it('looks the summary up when the stale row never carried one', async () => {
    // The snapshot was wrong about the transcript, so its silence about the
    // summary is worth nothing either — and returning `status_unknown` on
    // an asset we just proved is processed would strand it.
    transcribeMock.mockResolvedValue(TRANSCRIBE_ALREADY);
    fetchResourceByIdMock.mockResolvedValue({
      id: 'r-53', transcript_status: 'completed', summary_status: 'none',
    });

    const result = await ensureResourceProcessed({
      id: 'r-53', kind: 'video', transcript_status: 'none',
    });

    expect(fetchResourceByIdMock).toHaveBeenCalledWith('r-53');
    expect(summaryMock).toHaveBeenCalledWith('r-53');
    expect(result.action).toBe('triggered_summary');
  });

  it('still trusts a known-completed summary without a second call', async () => {
    transcribeMock.mockResolvedValue(TRANSCRIBE_ALREADY);

    const result = await ensureResourceProcessed({
      id: 'r-54', kind: 'video', transcript_status: 'none', summary_status: 'completed',
    });

    expect(summaryMock).not.toHaveBeenCalled();
    expect(result.action).toBe('ready');
    expect(result.alreadyTranscribed).toBe(true);
  });
});
