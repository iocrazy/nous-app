/**
 * Cover Studio's generation client.
 *
 * The properties worth pinning:
 *   1. Stage 1 and stage 2 hit the SAME endpoint with different bodies; stage 2
 *      carries the picked draft. Sending stage 1's body for a refine would
 *      silently redraw the grid instead of the cover.
 *   2. `prompt` comes back from the server and is passed through untouched —
 *      it feeds the "What was sent to the model" panel, and a second copy built
 *      here would be a second truth.
 *   3. `generated_media_id` arrives as a JSON **number** on this route and must
 *      be stringified at the boundary. Everything else in Cover Studio
 *      addresses generated media by string id.
 *   4. "completed with no image" is NOT success — reporting it as one puts an
 *      empty slot on screen with nothing to explain it.
 *   5. A refusal carries the server's own sentence, not a generic one.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockApiFetch = vi.fn();
vi.mock('./apiClient', () => ({ apiFetch: (...a: unknown[]) => mockApiFetch(...a) }));

const mockPoll = vi.fn();
vi.mock('../features/canvas-core/services/canvasGenerationService', () => ({
  pollGeneration: (...a: unknown[]) => mockPoll(...a),
}));

import {
  CoverStudioError,
  awaitCoverGeneration,
  generateCoverDrafts,
  refineCoverDraft,
} from './coverStudioService';

const PROMPT = 'Create one 2x2 preview grid image. …';

function ok(body: unknown) {
  mockApiFetch.mockResolvedValueOnce({ json: async () => body });
}

beforeEach(() => vi.clearAllMocks());

describe('generateCoverDrafts', () => {
  it('POSTs stage 1 and returns the server-built prompt untouched', async () => {
    ok({ task_id: 't1', prompt: PROMPT, aspect: '3:4', reference_count: 2 });

    const res = await generateCoverDrafts({
      topic: '三分钟看懂内容差',
      sourceUrls: ['/api/v1/generated-media/1/cover'],
    });

    expect(mockApiFetch).toHaveBeenCalledWith(
      '/api/v1/distribution/covers/generate',
      expect.objectContaining({
        method: 'POST',
        json: expect.objectContaining({ stage: 1, topic: '三分钟看懂内容差' }),
      }),
    );
    // Verbatim: this exact string is what the disclosure panel shows.
    expect(res.prompt).toBe(PROMPT);
  });

  it('defaults the small-labels toggle to OFF', async () => {
    // The two source files disagree; off follows SKILL.md, which forbids them.
    ok({ task_id: 't1', prompt: PROMPT, aspect: '3:4', reference_count: 0 });

    await generateCoverDrafts({ topic: 'x', sourceUrls: [] });

    const body = mockApiFetch.mock.calls[0][1].json;
    expect(body.allow_small_labels).toBe(false);
  });

  it('sends the pool order as given', async () => {
    // The person reference goes first so "the supplied character image" in the
    // prompt is unambiguous. Re-sorting here would break that.
    const urls = ['/api/v1/generated-media/9/cover', '/api/v1/generated-media/3/cover'];
    ok({ task_id: 't1', prompt: PROMPT, aspect: '3:4', reference_count: 2 });

    await generateCoverDrafts({ topic: 'x', sourceUrls: urls });

    expect(mockApiFetch.mock.calls[0][1].json.source_urls).toEqual(urls);
  });
});

describe('refineCoverDraft', () => {
  it('POSTs stage 2 WITH the picked draft', async () => {
    ok({ task_id: 't2', prompt: 'refine…', aspect: '3:4', reference_count: 3 });

    await refineCoverDraft({
      topic: 'x',
      sourceUrls: [],
      selectedDraft: 2,
      headline: '内容差的真相',
    });

    const body = mockApiFetch.mock.calls[0][1].json;
    expect(body.stage).toBe(2);
    expect(body.selected_draft).toBe(2);
    expect(body.headline).toBe('内容差的真相');
  });
});

describe('failures', () => {
  it('maps a 422 to invalid-input and keeps the server sentence', async () => {
    mockApiFetch.mockRejectedValueOnce(
      Object.assign(new Error('generic'), {
        status: 422,
        details: { message: 'selected_draft must be 1-4, got None' },
      }),
    );

    await expect(
      refineCoverDraft({ topic: 'x', sourceUrls: [], selectedDraft: 9 }),
    ).rejects.toMatchObject({
      failure: 'invalid-input',
      // The server's own reason survives — it is the only place a specific
      // cause exists at all.
      message: 'selected_draft must be 1-4, got None',
    });
  });

  it('maps a 404 to module-off', async () => {
    mockApiFetch.mockRejectedValueOnce(Object.assign(new Error('nope'), { status: 404 }));

    await expect(
      generateCoverDrafts({ topic: 'x', sourceUrls: [] }),
    ).rejects.toMatchObject({ failure: 'module-off' });
  });

  it('maps a transport failure (no status) to network', async () => {
    mockApiFetch.mockRejectedValueOnce(new Error('offline'));

    await expect(
      generateCoverDrafts({ topic: 'x', sourceUrls: [] }),
    ).rejects.toBeInstanceOf(CoverStudioError);
  });
});

describe('awaitCoverGeneration', () => {
  it('reports the image url and stringifies the numeric id', async () => {
    // ⚠️ Real wire shape: task_tracking.metadata is written by the workflow,
    // which puts the RAW id in — a JSON number, not a string.
    mockPoll.mockResolvedValueOnce({
      phase: 'completed',
      metadata: {
        result_url: '/api/v1/generated-media/341582104263581/cover',
        generated_media_id: 341582104263581,
      },
    });

    const out = await awaitCoverGeneration('t1');

    expect(out.ok).toBe(true);
    expect(out.url).toBe('/api/v1/generated-media/341582104263581/cover');
    expect(out.generatedMediaId).toBe('341582104263581');
    expect(typeof out.generatedMediaId).toBe('string');
  });

  it('treats "completed but no image" as a failure, not a success', async () => {
    mockPoll.mockResolvedValueOnce({ phase: 'completed', metadata: {} });

    const out = await awaitCoverGeneration('t1');

    expect(out.ok).toBe(false);
    expect(out.error).toMatch(/without an image/);
  });

  it('carries the server error message on a failed generation', async () => {
    mockPoll.mockResolvedValueOnce({
      phase: 'failed',
      error_msg: 'no_credit: subscription quota exhausted',
    });

    const out = await awaitCoverGeneration('t1');

    expect(out.ok).toBe(false);
    expect(out.error).toContain('no_credit');
  });

  it('names the phase when a generation ends without an error message', async () => {
    // cancelled/lost carry no error_msg. "generation cancelled" still beats an
    // empty string, which would render as a blank error box.
    mockPoll.mockResolvedValueOnce({ phase: 'cancelled' });

    const out = await awaitCoverGeneration('t1');

    expect(out.ok).toBe(false);
    expect(out.error).toBe('generation cancelled');
  });
});
