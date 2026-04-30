// HTTP implementation of the AiGateway interface.
// Calls backend storyboard AI endpoints for image generation.
//
// Backend routes (sb_ai_router.py, prefix="/storyboard"):
//   POST /storyboard/generate/image
//   POST /storyboard/generate/video
// Task status is tracked via task_tracking (task_manager_router):
//   GET /tasks/{task_id}   (Celery status)

import type { AiGateway, GenerateImagePayload } from '../application/ports';
import { getAuthHeaders } from '../../../services/parserService';

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

async function handleJsonResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`AI gateway error ${res.status}: ${text}`);
  }
  return (await res.json()) as T;
}

/** Unwrap backend `{ success, data }` envelope. */
function unwrapEnvelope<T>(body: { success: boolean; data?: T; task_id?: string }): T {
  if ('data' in body && body.data !== undefined) {
    return body.data;
  }
  // Some endpoints return { success, task_id } without nesting in data
  return body as unknown as T;
}

export class HttpAiGateway implements AiGateway {
  private readonly apiBase: string;

  constructor(apiBase?: string) {
    this.apiBase = apiBase || getApiUrl();
  }

  async setApiKey(_provider: string, _apiKey: string): Promise<void> {
    // In the web version, API keys are managed server-side.
    console.info('[HttpAiGateway] setApiKey is a no-op in web mode; keys are server-managed.');
  }

  async generateImage(payload: GenerateImagePayload): Promise<string> {
    const headers = await getAuthHeaders();
    const res = await fetch(`${this.apiBase}/api/v1/storyboard/generate/image`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        project_id: payload.projectId,
        node_id: payload.nodeId,
        prompt: payload.prompt,
        model: payload.model,
        provider: payload.provider ?? 'replicate',
        aspect_ratio: payload.aspectRatio,
        character_ids: payload.characterIds ?? [],
        reference_image_url: payload.referenceImageUrl,
      }),
    });

    const body = await handleJsonResponse<{ success: boolean; task_id: string; image_url?: string }>(res);

    // If the endpoint returns an image_url directly (synchronous generation),
    // return it. Otherwise, return the task_id for async polling.
    if (body.image_url) {
      return body.image_url;
    }

    return body.task_id;
  }

  async submitGenerateImageJob(payload: GenerateImagePayload): Promise<string> {
    const headers = await getAuthHeaders();
    const res = await fetch(`${this.apiBase}/api/v1/storyboard/generate/image`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        project_id: payload.projectId,
        node_id: payload.nodeId,
        prompt: payload.prompt,
        model: payload.model,
        provider: payload.provider ?? 'replicate',
        aspect_ratio: payload.aspectRatio,
        character_ids: payload.characterIds ?? [],
        reference_image_url: payload.referenceImageUrl,
      }),
    });

    const body = await handleJsonResponse<{ success: boolean; task_id: string }>(res);
    return body.task_id;
  }

  async getGenerateImageJob(jobId: string): Promise<{
    job_id: string;
    status: 'queued' | 'running' | 'succeeded' | 'failed' | 'not_found';
    result?: string | null;
    error?: string | null;
  }> {
    const headers = await getAuthHeaders();
    // Use the Celery task status endpoint
    const res = await fetch(`${this.apiBase}/api/v1/tasks/${jobId}`, {
      headers,
    });

    if (!res.ok) {
      return { job_id: jobId, status: 'not_found' };
    }

    const body = await handleJsonResponse<{
      task_id: string;
      status: string;
      result?: Record<string, unknown> | null;
      error?: string | null;
    }>(res);

    // Map Celery statuses to our gateway statuses
    const statusMap: Record<string, 'queued' | 'running' | 'succeeded' | 'failed' | 'not_found'> = {
      PENDING: 'queued',
      STARTED: 'running',
      SUCCESS: 'succeeded',
      FAILURE: 'failed',
      RETRY: 'running',
      REVOKED: 'failed',
    };

    const mappedStatus = statusMap[body.status] ?? 'queued';
    const imageUrl = body.result && typeof body.result === 'object'
      ? (body.result as Record<string, unknown>).image_url as string | undefined
      : undefined;

    return {
      job_id: jobId,
      status: mappedStatus,
      result: imageUrl ?? null,
      error: body.error ?? null,
    };
  }
}
