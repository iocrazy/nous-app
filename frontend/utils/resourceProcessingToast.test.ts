/**
 * resourceProcessingNotice — what the user is told after attaching an
 * asset that needed processing.
 *
 * Two of these are money-visible: "we just spent N points on you" and "we
 * spent nothing because it was already running" are different sentences
 * and must not collapse into one. The third is the typed failure回显 the
 * repo requires of every user-action→agent trigger path: a silent no-op is
 * not acceptable.
 */
import { describe, it, expect } from 'vitest';
import { resourceProcessingNotice } from './resourceProcessingToast';

/** Mirrors i18next's (key, default, opts) call shape used across the app. */
const t = (_key: string, def: string, opts?: Record<string, unknown>) =>
  def.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(opts?.[name] ?? ''));

describe('resourceProcessingNotice', () => {
  it('says nothing when nothing happened', () => {
    expect(resourceProcessingNotice({ action: 'ready' }, t)).toBeNull();
    expect(resourceProcessingNotice({ action: 'skipped' }, t)).toBeNull();
  });

  it('names the cost of a freshly queued transcription', () => {
    const notice = resourceProcessingNotice(
      { action: 'triggered_transcribe', pointsCharged: 5, alreadyInProgress: false },
      t,
    );

    expect(notice?.type).toBe('info');
    expect(notice?.message).toContain('5');
    expect(notice?.message.toLowerCase()).toContain('transcrib');
  });

  it('does not claim a charge when the backend deduped an in-flight task', () => {
    const notice = resourceProcessingNotice(
      {
        action: 'triggered_transcribe',
        pointsCharged: 0,
        alreadyInProgress: true,
        message: 'Transcription already in progress',
      },
      t,
    );

    expect(notice?.type).toBe('info');
    expect(notice?.message.toLowerCase()).toContain('already');
    // The "N points" sentence must not be reachable on this arm.
    expect(notice?.message).not.toContain('0 points');
  });

  it('announces a queued summary without inventing a price', () => {
    const notice = resourceProcessingNotice({ action: 'triggered_summary' }, t);

    expect(notice?.type).toBe('info');
    expect(notice?.message.toLowerCase()).toContain('summar');
    expect(notice?.message).not.toMatch(/\d+\s*points/i);
  });

  it('surfaces a failed trigger as an error carrying the reason', () => {
    const notice = resourceProcessingNotice(
      { action: 'failed', attempted: 'transcribe', error: 'HTTP 402 Insufficient points' },
      t,
    );

    expect(notice?.type).toBe('error');
    expect(notice?.message).toContain('Insufficient points');
  });

  it('still reports a failure that came with no error text', () => {
    const notice = resourceProcessingNotice({ action: 'failed', attempted: 'summary' }, t);

    expect(notice?.type).toBe('error');
    expect(notice?.message.length).toBeGreaterThan(0);
  });
});

describe('resourceProcessingNotice — unresolvable status', () => {
  it('tells the user nothing was processed when the status could not be read', () => {
    const notice = resourceProcessingNotice(
      { action: 'status_unknown', error: 'HTTP 404' },
      t,
    );

    // Visible, and honest about what did NOT happen — this arm is the one
    // that trades "maybe re-bill" for "maybe do nothing".
    expect(notice).not.toBeNull();
    expect(notice?.message.length).toBeGreaterThan(0);
    expect(notice?.message).not.toMatch(/\d+\s*points/i);
  });
});

describe('resourceProcessingNotice — blocked behind an audio extraction', () => {
  it('tells the user to retry rather than to wait', () => {
    const notice = resourceProcessingNotice({ action: 'pending_audio', pointsCharged: 0 }, t);

    expect(notice?.type).toBe('info');
    expect(notice?.message.toLowerCase()).toMatch(/again|retry/);
    // Must NOT reuse the "already being processed" sentence: nothing is
    // processing this asset.
    expect(notice?.message.toLowerCase()).not.toContain('already being processed');
  });
});

describe('resourceProcessingNotice — finished work, stale snapshot', () => {
  it('says the transcript was already there rather than implying a charge', () => {
    const notice = resourceProcessingNotice(
      { action: 'triggered_summary', alreadyTranscribed: true },
      t,
    );

    expect(notice?.type).toBe('info');
    expect(notice?.message.toLowerCase()).toContain('already transcribed');
    expect(notice?.message.toLowerCase()).toContain('no extra points');
  });

  it('keeps the plain summarising sentence when nothing was short-circuited', () => {
    const notice = resourceProcessingNotice({ action: 'triggered_summary' }, t);

    expect(notice?.message.toLowerCase()).not.toContain('already transcribed');
  });
});
