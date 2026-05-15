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
    // Hits the DBOS workflow status endpoint. The legacy /api/v1/tasks/
    // endpoint is now a 410 Gone tombstone (Celery removed in PR-D7) —
    // calling it silently turned every job into status='not_found' and
    // the canvas never saw real progress (issue #282).
    const res = await fetch(
      `${this.apiBase}/api/v1/workflows/${encodeURIComponent(jobId)}/status`,
      { headers },
    );

    if (res.status === 404) {
      return { job_id: jobId, status: 'not_found' };
    }
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      return {
        job_id: jobId,
        status: 'failed',
        error: text || `HTTP ${res.status}`,
      };
    }

    const body = await handleJsonResponse<{
      workflow_id?: string;
      status?: string;
      output?: Record<string, unknown> | string | null;
      error?: string | null;
    }>(res);

    // Map DBOS WorkflowStatusString → gateway statuses. Source of truth:
    // backend mirror_dbos_lifecycle_to_tracking trigger (mig 184).
    const statusMap: Record<string, 'queued' | 'running' | 'succeeded' | 'failed' | 'not_found'> = {
      PENDING: 'queued',
      ENQUEUED: 'queued',
      STARTED: 'running',
      RUNNING: 'running',
      SUCCESS: 'succeeded',
      ERROR: 'failed',
      RETRIES_EXCEEDED: 'failed',
      MAX_RECOVERY_ATTEMPTS_EXCEEDED: 'failed',
      CANCELLED: 'failed',
    };

    const mappedStatus = statusMap[body.status ?? ''] ?? 'queued';

    // storyboard_image_workflow returns {status, node_id, result: <url>}.
    // The DBOS status endpoint exposes that as `output`. Pull the URL out.
    let imageUrl: string | undefined;
    const out = body.output;
    if (out && typeof out === 'object') {
      const outRec = out as Record<string, unknown>;
      const candidate = outRec.result ?? outRec.image_url;
      if (typeof candidate === 'string') {
        imageUrl = candidate;
      }
    } else if (typeof out === 'string') {
      imageUrl = out;
    }

    return {
      job_id: jobId,
      status: mappedStatus,
      result: imageUrl ?? null,
      error: body.error ?? null,
    };
  }
}
