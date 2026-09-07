import { describe, it, expect } from 'vitest';
import { humanizeTaskError } from './humanizeTaskError';

describe('humanizeTaskError', () => {
  it('maps the Volcengine ASR "Invalid audio URI" chain (the reported bug)', () => {
    const raw =
      'DBOSMaxStepRetriesExceeded: RuntimeError: Volcengine ASR query failed: ' +
      '45000006 [Invalid audio URI] OperatorWrapper Process failed: internal ' +
      'error,audio download failed';
    const r = humanizeTaskError(raw);
    expect(r.message).toBe("Transcription couldn't read this media's audio.");
    expect(r.hint).toMatch(/background music download to finish/i);
  });

  it('maps the ffmpeg no-audio-stream chain (extract→transcribe on a silent video)', () => {
    const raw =
      'RuntimeError: audio extraction errored for BV1xx: ffmpeg rc=234 ' +
      'Output file #0 does not contain any stream';
    expect(humanizeTaskError(raw).message).toBe(
      'This video has no audio track — it may have been downloaded without sound.',
    );
  });

  it('maps insufficient balance / HTTP 402', () => {
    expect(humanizeTaskError('Insufficient Balance').message).toBe(
      'The AI provider account is out of balance.',
    );
    expect(humanizeTaskError('HTTP 402 Payment Required').message).toBe(
      'The AI provider account is out of balance.',
    );
  });

  it('maps resource-not-granted / 45000030', () => {
    expect(humanizeTaskError('ASR error 45000030 resource not granted').message).toBe(
      "This model isn't enabled for the configured provider account.",
    );
  });

  it('maps OpenAI-style 404 model_not_found / no active grant (not "source not found")', () => {
    const raw =
      'NotFoundError: Error code: 404 - {\'error\': {\'message\': "no active ' +
      "grant for service 'whisper-1' on this key\", 'type': 'not_found_error', " +
      "'code': 'model_not_found'}}";
    expect(humanizeTaskError(raw).message).toBe(
      "This model isn't enabled for the configured provider account.",
    );
  });

  it('maps a transient 429 to "try again shortly"', () => {
    expect(humanizeTaskError('HTTP 429 Too Many Requests').message).toBe(
      'The AI provider is rate-limiting — try again shortly.',
    );
  });

  // Changed 2026-08-19: SetLimitExceeded used to share the generic 429 copy.
  // It is NOT transient — it is an inference cap configured on the provider
  // account, and "try again shortly" sent users to wait out something that
  // never clears. doubao-seed-2-0-pro returned it on 126 consecutive hourly
  // health probes across five days while every ai_summary run failed.
  it('separates a configured account cap from a transient rate limit', () => {
    const raw =
      'DBOSMaxStepRetriesExceeded: all 1 model(s) failed: ' +
      'doubao-seed-2-0-pro-260215 (HTTPStatusError HTTP 429: ' +
      '{"error":{"code":"SetLimitExceeded","message":"Your account has reached ' +
      'the set inference limit for the [doubao-seed-2-0-pro] model"}})';
    const { message, hint } = humanizeTaskError(raw);

    expect(message).toBe(
      'The provider account has hit its configured limit for this model.',
    );
    expect(hint).toMatch(/provider console|another model/i);
  });

  it('maps timeouts', () => {
    expect(humanizeTaskError('TimeoutError: request timed out').message).toBe(
      'The task timed out — retry usually works.',
    );
  });

  it('maps unavailable source track and missing source', () => {
    expect(
      humanizeTaskError('SodaApiError: track 1 returned no url_player_info').message,
    ).toBe("This media's audio track is no longer available.");
    expect(humanizeTaskError('HTTP 404 not found').message).toBe(
      "The source media couldn't be found.",
    );
  });

  it('strips the DBOSMaxStepRetriesExceeded wrapper before matching', () => {
    // Inner text is a short clean sentence: after stripping the wrapper it is
    // passed through verbatim, proving the prefix was removed.
    const r = humanizeTaskError('DBOSMaxStepRetriesExceeded: queue drained cleanly');
    expect(r.message).toBe('queue drained cleanly');
  });

  it('keeps already-clean short strings as the message (no regression)', () => {
    // e.g. distribution publish errors are already human-readable.
    expect(humanizeTaskError('upload rejected').message).toBe('upload rejected');
  });

  it('falls back to a neutral message for raw technical dumps, preserving detail elsewhere', () => {
    const r = humanizeTaskError('KeyError: totally novel internal blowup 9x');
    expect(r.message).toBe('Processing failed — see details.');
    expect(r.hint).toBeUndefined();
  });

  it('handles empty / missing input', () => {
    expect(humanizeTaskError(undefined).message).toBe('Processing failed.');
    expect(humanizeTaskError('').message).toBe('Processing failed.');
    expect(humanizeTaskError('   ').message).toBe('Processing failed.');
  });
});

describe('humanizeTaskError — provider card switched off', () => {
  it('names the card and where to turn it on', () => {
    const r = humanizeTaskError('RuntimeError: [provider_card_disabled] Codex (Local CLI) is switched off');
    expect(r.message).toMatch(/Codex \(Local CLI\)/);
    expect(r.hint ?? '').toMatch(/Settings → AI → Providers/);
  });
});

describe('humanizeTaskError — object store write failed', () => {
  it('names the storage outage and says nothing was saved, whatever the wrapper', () => {
    for (const raw of [
      'Storage is unavailable, so nothing was saved. Try again in a moment.',
      'ObjectStoreWriteFailed: Storage is unavailable, so nothing was saved. Try again in a moment.',
      'DBOSMaxStepRetriesExceeded: RuntimeError: [object_store_write_failed] where=promote_generated_media',
    ]) {
      const r = humanizeTaskError(raw);
      expect(r.message).toBe('Storage is unavailable, so nothing was saved.');
      expect(r.hint ?? '').toMatch(/try again/i);
    }
  });
});
