import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  resolveResourceThumbnailSrc,
  resourceProcessingState,
  resolveChipProcessingState,
} from './resourceStatus';

describe('resolveResourceThumbnailSrc', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_API_URL', 'https://api.example.test');
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('returns null when there is no thumbnail', () => {
    expect(resolveResourceThumbnailSrc(null)).toBeNull();
    expect(resolveResourceThumbnailSrc(undefined)).toBeNull();
    expect(resolveResourceThumbnailSrc('')).toBeNull();
  });

  it('prefixes the API base onto the relative path the backend sends', () => {
    // Task 1 contract: `/api/v1/resources/{id}/cover`, RELATIVE — the
    // frontend owns the base. Rendering the raw value would hit the
    // Cloudflare Pages origin, which serves index.html for unknown paths
    // (the same `_redirects` catch-all trap called out in CLAUDE.md).
    expect(resolveResourceThumbnailSrc('/api/v1/resources/77/cover')).toBe(
      'https://api.example.test/api/v1/resources/77/cover',
    );
  });

  it('leaves an already-absolute URL untouched', () => {
    expect(resolveResourceThumbnailSrc('https://cdn.example.test/a.jpg')).toBe(
      'https://cdn.example.test/a.jpg',
    );
    expect(resolveResourceThumbnailSrc('data:image/png;base64,AAA')).toBe(
      'data:image/png;base64,AAA',
    );
  });
});

describe('resourceProcessingState', () => {
  it('is null for kinds that have nothing to transcribe', () => {
    expect(resourceProcessingState({ kind: 'doc' })).toBeNull();
    expect(resourceProcessingState({ kind: 'image', mime: 'image/png' })).toBeNull();
  });

  it('detects audio-visual by mime when kind is missing', () => {
    expect(resourceProcessingState({ mime: 'video/mp4', transcriptStatus: 'none' })).toBe('unprocessed');
    expect(resourceProcessingState({ mime: 'audio/mpeg', transcriptStatus: 'none' })).toBe('unprocessed');
  });

  it('says nothing at all when the status columns were never supplied', () => {
    // "we were not told" is not "we were told there is nothing". A caller
    // that hands over {id, name, kind} only (the context-menu path) must not
    // make the chip claim an already-transcribed video is unprocessed.
    expect(resourceProcessingState({ kind: 'video' })).toBeNull();
    expect(resourceProcessingState({ kind: 'video', transcriptStatus: '', summaryStatus: '' })).toBeNull();
    expect(resourceProcessingState({ kind: 'audio', transcriptStatus: null })).toBeNull();
    // Transcript known-done but summary never supplied → still no claim.
    expect(resourceProcessingState({ kind: 'video', transcriptStatus: 'completed' })).toBeNull();
  });

  it('flags an untranscribed video as unprocessed', () => {
    // Only an EXPLICIT backend verdict earns the dot — 'none' means the
    // column was read and it is empty, 'failed' means a run went wrong.
    expect(resourceProcessingState({ kind: 'video', transcriptStatus: 'none' })).toBe('unprocessed');
    expect(resourceProcessingState({ kind: 'video', transcriptStatus: 'failed' })).toBe('unprocessed');
  });

  it('flags an in-flight transcription as processing', () => {
    expect(resourceProcessingState({ kind: 'video', transcriptStatus: 'processing' })).toBe('processing');
    expect(resourceProcessingState({ kind: 'video', transcriptStatus: 'pending' })).toBe('processing');
  });

  it('walks on to the summary once the transcript is complete', () => {
    expect(
      resourceProcessingState({ kind: 'video', transcriptStatus: 'completed', summaryStatus: 'none' }),
    ).toBe('unprocessed');
    expect(
      resourceProcessingState({ kind: 'video', transcriptStatus: 'completed', summaryStatus: 'processing' }),
    ).toBe('processing');
  });

  it('is null when both steps are done', () => {
    expect(
      resourceProcessingState({ kind: 'video', transcriptStatus: 'completed', summaryStatus: 'completed' }),
    ).toBeNull();
  });

  it("treats the backend's `skipped` as nothing-to-do, not as missing", () => {
    // `skipped` means "there is nothing here to process" (e.g. no audio
    // track). Painting a warn dot would nag the user about work that will
    // never happen — and ensureResourceProcessed refuses to trigger it too.
    expect(resourceProcessingState({ kind: 'video', transcriptStatus: 'skipped' })).toBeNull();
    expect(
      resourceProcessingState({ kind: 'audio', transcriptStatus: 'completed', summaryStatus: 'skipped' }),
    ).toBeNull();
  });
});

const task = (over: Record<string, unknown> = {}) => ({
  task_type: 'ai_transcription',
  resource_id: '77',
  status: 'processing',
  created_at: '2026-08-17T00:00:00Z',
  ...over,
});

describe('resolveChipProcessingState', () => {
  it('falls back to the insert-time snapshot when the task list is unavailable', () => {
    // No TaskManagerProvider (fullscreen editor routes) → null tasks.
    expect(resolveChipProcessingState('unprocessed', null, '77')).toBe('unprocessed');
    expect(resolveChipProcessingState(null, undefined, '77')).toBeNull();
  });

  it('falls back to the snapshot when no task matches this resource', () => {
    expect(resolveChipProcessingState('unprocessed', [task({ resource_id: '99' })] as never, '77')).toBe(
      'unprocessed',
    );
  });

  it('reports processing while a matching task is still running', () => {
    expect(resolveChipProcessingState(null, [task({ status: 'processing' })] as never, '77')).toBe('processing');
    expect(resolveChipProcessingState(null, [task({ status: 'pending' })] as never, '77')).toBe('processing');
  });

  it('matches resource_id across the number/string wire split', () => {
    // Snowflake ids arrive as JSON numbers on some routers and strings on
    // others (CLAUDE.md). A === on the raw values silently never matches.
    expect(resolveChipProcessingState(null, [task({ resource_id: 77 })] as never, '77')).toBe('processing');
  });

  it('clears the snapshot once the newest matching task has completed', () => {
    const tasks = [
      task({ status: 'processing', created_at: '2026-08-17T00:00:00Z' }),
      task({ status: 'completed', created_at: '2026-08-17T01:00:00Z' }),
    ];
    expect(resolveChipProcessingState('unprocessed', tasks as never, '77')).toBeNull();
  });

  it('reports unprocessed when the newest matching task failed', () => {
    expect(
      resolveChipProcessingState(null, [task({ status: 'failed' })] as never, '77'),
    ).toBe('unprocessed');
  });

  it('watches the summary and audio-extraction tasks too, not just transcription', () => {
    expect(resolveChipProcessingState(null, [task({ task_type: 'ai_summary' })] as never, '77')).toBe(
      'processing',
    );
    expect(resolveChipProcessingState(null, [task({ task_type: 'extract_audio' })] as never, '77')).toBe(
      'processing',
    );
  });

  it('ignores unrelated task types for the same resource', () => {
    expect(
      resolveChipProcessingState(null, [task({ task_type: 'download' })] as never, '77'),
    ).toBeNull();
  });
});
