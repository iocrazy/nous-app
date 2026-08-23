// frontend/services/coverStudioService.ts
//
// Cover Studio's two-stage generation.
//
//   stage 1  one 2x2 grid image = four drafts   (one generation, not four)
//   stage 2  the picked draft, redrawn full size
//
// That shape is the skill's Core Rule, not an optimisation of ours: a round of
// ideas costs one image, and only the draft the user picked gets redrawn.
//
// ★ The prompt is built SERVER-SIDE and echoed back. The design shows it behind
// a "What was sent to the model" disclosure, and building a second copy here
// that is meant to match would be two truths that drift. `prompt` in the
// response IS the string that was dispatched — the backend asserts that too.

import { apiFetch } from './apiClient';
import {
  pollGeneration,
  type GenerationTask,
} from '../features/canvas-core/services/canvasGenerationService';

export interface CoverGenerateResult {
  task_id: string;
  /** Verbatim, for the "What was sent to the model" panel. */
  prompt: string;
  aspect: string;
  reference_count: number;
}

export type CoverStudioFailure =
  | 'invalid-input'
  | 'module-off'
  | 'unauthenticated'
  | 'server'
  | 'network';

/**
 * Typed failure. Throwing rather than returning a union is deliberate — a
 * caller that forgets to branch gets a loud rejection, never a silent no-op.
 * The generate button spends the user's own image quota; "nothing happened"
 * is the one response it must never give.
 */
export class CoverStudioError extends Error {
  readonly failure: CoverStudioFailure;
  readonly status?: number;

  constructor(failure: CoverStudioFailure, message: string, status?: number) {
    super(message);
    this.name = 'CoverStudioError';
    this.failure = failure;
    this.status = status;
  }
}

function failureFromStatus(status: number): CoverStudioFailure {
  if (status === 404) return 'module-off';
  if (status === 401) return 'unauthenticated';
  if (status === 403) return 'unauthenticated';
  if (status === 422 || status === 400) return 'invalid-input';
  return 'server';
}

async function post<T>(path: string, json: unknown): Promise<T> {
  let response: Response;
  try {
    response = await apiFetch(path, { method: 'POST', json });
  } catch (err) {
    const status = (err as { status?: number })?.status;
    if (typeof status === 'number') {
      // The server's own sentence beats a generic one — it is the only place a
      // specific reason (blank topic, draft out of range) exists at all.
      const detail =
        (err as { details?: { message?: string } })?.details?.message ??
        (err as Error).message;
      throw new CoverStudioError(failureFromStatus(status), detail, status);
    }
    throw new CoverStudioError('network', (err as Error)?.message || 'network error');
  }
  return (await response.json()) as T;
}

export interface CoverGenerateInput {
  topic: string;
  /** generated_media reference URLs, in pool order (person first). */
  sourceUrls: string[];
  model?: string;
  quality?: string;
  allowSmallLabels?: boolean;
}

/** Stage 1 — the 2x2 grid of four drafts. */
export async function generateCoverDrafts(
  input: CoverGenerateInput,
): Promise<CoverGenerateResult> {
  return post<CoverGenerateResult>('/api/v1/distribution/covers/generate', {
    stage: 1,
    topic: input.topic,
    model: input.model ?? '',
    quality: input.quality ?? 'high',
    source_urls: input.sourceUrls,
    allow_small_labels: input.allowSmallLabels ?? false,
  });
}

/** Stage 2 — redraw the picked draft at full size. */
export async function refineCoverDraft(
  input: CoverGenerateInput & { selectedDraft: number; headline?: string },
): Promise<CoverGenerateResult> {
  return post<CoverGenerateResult>('/api/v1/distribution/covers/generate', {
    stage: 2,
    topic: input.topic,
    model: input.model ?? '',
    quality: input.quality ?? 'high',
    source_urls: input.sourceUrls,
    allow_small_labels: input.allowSmallLabels ?? false,
    selected_draft: input.selectedDraft,
    headline: input.headline ?? '',
  });
}

export interface CoverGenerationOutcome {
  ok: boolean;
  /** `/api/v1/generated-media/{id}/cover` — also a valid reference URL. */
  url?: string;
  generatedMediaId?: string;
  /** Set when ok is false. Server-authored when there is one. */
  error?: string;
}

/**
 * Wait for one dispatched generation and report what came of it.
 *
 * Returns an outcome rather than throwing on a failed generation: "the model
 * refused this prompt" is a normal thing for the user to be told, not an
 * exception for the caller to handle. Transport problems still throw.
 *
 * ⚠️ `generated_media_id` arrives from the backend as a JSON **number** on this
 * route (task_tracking.metadata is written by the workflow, which puts the raw
 * id in). It is stringified here, at the boundary, because everything else in
 * Cover Studio addresses generated media by string id — mixing the two is the
 * exact drift that produced the 2026-08-12 storyboard incident.
 */
export async function awaitCoverGeneration(
  taskId: string,
  opts: { shouldStop?: () => boolean } = {},
): Promise<CoverGenerationOutcome> {
  const task: GenerationTask = await pollGeneration(taskId, {
    shouldStop: opts.shouldStop,
  });
  if (task.phase !== 'completed') {
    return {
      ok: false,
      error:
        task.error_msg ||
        `generation ${task.phase}`,
    };
  }
  const url = task.metadata?.result_url;
  if (!url) {
    // completed with no image is not success. Reporting it as one would put an
    // empty slot on screen with nothing to explain it.
    return { ok: false, error: 'the generation finished without an image' };
  }
  const rawId = task.metadata?.generated_media_id;
  return {
    ok: true,
    url,
    generatedMediaId: rawId === undefined || rawId === null ? undefined : String(rawId),
  };
}
