
import { AISettings, TranscriptData, SummaryData } from '../types';
import { getAuthHeaders } from './parserService';

// --- Polling Helper ---

export async function pollForResult<T>(
  fetcher: () => Promise<T>,
  interval: number = 3000,
  maxRetries: number = 10
): Promise<T> {
  for (let i = 0; i < maxRetries; i++) {
    await new Promise(resolve => setTimeout(resolve, interval));
    try {
      const result = await fetcher();
      return result;
    } catch (err: any) {
      // If the resource is not ready yet (404 or processing), keep polling
      if (i === maxRetries - 1) throw err;
      // If it's a non-retryable error (e.g., 500, 401), throw immediately
      const message = err?.message || '';
      if (message.includes('401') || message.includes('403')) throw err;
    }
  }
  throw new Error('Polling timed out');
}

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

// --- Transcription ---

export const triggerTranscription = async (
  platformId: string
): Promise<{ task_id: string }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/transcribe/${platformId}`, {
    method: 'POST',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

export const getTranscript = async (
  platformId: string
): Promise<TranscriptData> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/transcript/${platformId}`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const raw = await response.json();
  // Map backend field names to frontend interface
  return {
    text: raw.full_text || raw.text || '',
    segments: raw.segments || [],
    language: raw.language || '',
    duration: raw.duration_seconds ?? raw.duration ?? 0,
    created_at: raw.created_at || '',
  } as TranscriptData;
};

// --- Summary ---

export const triggerSummary = async (
  platformId: string
): Promise<{ task_id: string }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/summarize/${platformId}`, {
    method: 'POST',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

export const getSummary = async (
  platformId: string
): Promise<SummaryData> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/summary/${platformId}`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const raw = await response.json();
  // Map backend field names to frontend interface
  return {
    summary: raw.summary_text || raw.summary || '',
    key_points: raw.key_points || [],
    topics: raw.topics || [],
    created_at: raw.created_at || '',
  } as SummaryData;
};

// --- Visual Analysis ---

export const triggerVisualAnalysis = async (
  platformId: string
): Promise<{ task_id: string }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/analyze/${platformId}`, {
    method: 'POST',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

// --- Settings ---

export const getAISettings = async (): Promise<AISettings> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/settings`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

export const saveAISettings = async (
  settings: AISettings
): Promise<void> => {
  const apiUrl = getApiUrl();

  // Map frontend AISettings shape to backend AISettingsUpdate schema
  const backendPayload = {
    ai_providers: settings.providers,
    whisper_provider: settings.task_assignment?.transcription?.includes('openai') ? 'openai_api' : 'local',
    default_summary_model: settings.task_assignment?.summarization || 'gpt-4o-mini',
    default_analysis_model: settings.task_assignment?.visual_analysis || 'gpt-4o',
    // Include frontend-specific fields as extra data for persistence
    ai_enabled: settings.ai_enabled,
    auto_transcribe: settings.auto_transcribe,
    auto_summarize: settings.auto_summarize,
    preferred_language: settings.preferred_language,
    task_assignment: settings.task_assignment,
  };

  const response = await fetch(`${apiUrl}/api/v1/ai/settings`, {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: JSON.stringify(backendPayload),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }
};

export const testAIConnection = async (
  provider: string,
  config: { base_url?: string; api_key?: string }
): Promise<{ success: boolean; models?: string[]; error?: string }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/test-connection`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify({ provider_key: provider, ...config }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};
