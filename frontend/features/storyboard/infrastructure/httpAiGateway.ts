// HTTP implementation of the AiGateway interface.
// Calls backend storyboard AI endpoints for image generation.

import type { AiGateway, GenerateImagePayload } from '../application/ports';
import { getAuthHeaders } from '../../../services/parserService';

const getApiUrl = (): string => {
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    return (import.meta.env.VITE_API_URL as string) || '';
  }
  return 'http://localhost:8080';
};

async function handleJsonResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`AI gateway error ${res.status}: ${text}`);
  }
  const body = (await res.json()) as { success: boolean; data: T };
  return body.data;
}

export class HttpAiGateway implements AiGateway {
  private readonly apiBase: string;

  constructor(apiBase?: string) {
    this.apiBase = apiBase || getApiUrl();
  }

  async setApiKey(_provider: string, _apiKey: string): Promise<void> {
    // In the web version, API keys are managed server-side.
    // This is a no-op — the backend uses its own configured keys.
    console.info('[HttpAiGateway] setApiKey is a no-op in web mode; keys are server-managed.');
  }

  async generateImage(payload: GenerateImagePayload): Promise<string> {
    const headers = await getAuthHeaders();
    const res = await fetch(`${this.apiBase}/api/v1/storyboard/ai/generate-image`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        prompt: payload.prompt,
        model: payload.model,
        size: payload.size,
        aspect_ratio: payload.aspectRatio,
        reference_images: payload.referenceImages,
        extra_params: payload.extraParams,
      }),
    });

    const result = await handleJsonResponse<{ task_id: string; image_url?: string }>(res);

    // If the endpoint returns an image_url directly (synchronous generation),
    // return it. Otherwise, return the task_id for async polling.
    if (result.image_url) {
      return result.image_url;
    }

    return result.task_id;
  }

  async submitGenerateImageJob(payload: GenerateImagePayload): Promise<string> {
    const headers = await getAuthHeaders();
    const res = await fetch(`${this.apiBase}/api/v1/storyboard/ai/generate-image`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        prompt: payload.prompt,
        model: payload.model,
        size: payload.size,
        aspect_ratio: payload.aspectRatio,
        reference_images: payload.referenceImages,
        extra_params: payload.extraParams,
      }),
    });

    const result = await handleJsonResponse<{ task_id: string }>(res);
    return result.task_id;
  }

  async getGenerateImageJob(jobId: string): Promise<{
    job_id: string;
    status: 'queued' | 'running' | 'succeeded' | 'failed' | 'not_found';
    result?: string | null;
    error?: string | null;
  }> {
    const headers = await getAuthHeaders();
    const res = await fetch(`${this.apiBase}/api/v1/storyboard/ai/jobs/${jobId}`, {
      headers,
    });

    return await handleJsonResponse<{
      job_id: string;
      status: 'queued' | 'running' | 'succeeded' | 'failed' | 'not_found';
      result?: string | null;
      error?: string | null;
    }>(res);
  }
}
