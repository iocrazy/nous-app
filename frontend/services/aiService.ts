import { AISettings, TranscriptData, SummaryData, NousModelPublic } from '../types';
import { apiClient } from './apiClient';

// --- Polling Helper ---

export async function pollForResult<T>(
  fetcher: () => Promise<T>,
  interval: number = 3000,
  maxRetries: number = 10,
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

// --- Transcription ---

/** @deprecated Use triggerTranscriptionByResource instead */
export const triggerTranscription = async (
  platformId: string,
): Promise<{ task_id: string }> =>
  apiClient.post<{ task_id: string }>(`/api/v1/ai/transcribe/${platformId}`);

export const triggerTranscriptionByResource = async (
  resourceId: string,
): Promise<{ task_id: string }> =>
  apiClient.post<{ task_id: string }>(
    `/api/v1/ai/transcribe/resource/${resourceId}`,
  );

interface RawTranscript {
  text?: string;
  full_text?: string;
  segments?: TranscriptData['segments'];
  language?: string;
  duration?: number;
  duration_seconds?: number;
  created_at?: string;
}

function toTranscript(raw: RawTranscript): TranscriptData {
  return {
    text: raw.full_text || raw.text || '',
    segments: raw.segments || [],
    language: raw.language || '',
    duration: raw.duration_seconds ?? raw.duration ?? 0,
    created_at: raw.created_at || '',
  } as TranscriptData;
}

/** @deprecated Use getTranscriptByResource instead */
export const getTranscript = async (
  platformId: string,
): Promise<TranscriptData> => {
  const raw = await apiClient.get<RawTranscript>(
    `/api/v1/ai/transcript/${platformId}`,
  );
  return toTranscript(raw);
};

export const getTranscriptByResource = async (
  resourceId: string,
): Promise<TranscriptData> => {
  const raw = await apiClient.get<RawTranscript>(
    `/api/v1/ai/transcript/resource/${resourceId}`,
  );
  return toTranscript(raw);
};

// --- Summary ---

/** @deprecated Use triggerSummaryByResource instead */
export const triggerSummary = async (
  platformId: string,
): Promise<{ task_id: string }> =>
  apiClient.post<{ task_id: string }>(`/api/v1/ai/summarize/${platformId}`);

export const triggerSummaryByResource = async (
  resourceId: string,
): Promise<{ task_id: string }> =>
  apiClient.post<{ task_id: string }>(
    `/api/v1/ai/summarize/resource/${resourceId}`,
  );

interface RawSummary {
  summary?: string;
  summary_text?: string;
  key_points?: string[];
  topics?: string[];
  created_at?: string;
}

function toSummary(raw: RawSummary): SummaryData {
  return {
    summary: raw.summary_text || raw.summary || '',
    key_points: raw.key_points || [],
    topics: raw.topics || [],
    created_at: raw.created_at || '',
  } as SummaryData;
}

/** @deprecated Use getSummaryByResource instead */
export const getSummary = async (platformId: string): Promise<SummaryData> => {
  const raw = await apiClient.get<RawSummary>(
    `/api/v1/ai/summary/${platformId}`,
  );
  return toSummary(raw);
};

export const getSummaryByResource = async (
  resourceId: string,
): Promise<SummaryData> => {
  const raw = await apiClient.get<RawSummary>(
    `/api/v1/ai/summary/resource/${resourceId}`,
  );
  return toSummary(raw);
};

// --- Visual Analysis ---

/** @deprecated Use triggerVisualAnalysisByResource instead */
export const triggerVisualAnalysis = async (
  platformId: string,
): Promise<{ task_id: string }> =>
  apiClient.post<{ task_id: string }>(`/api/v1/ai/analyze/${platformId}`);

export const triggerVisualAnalysisByResource = async (
  resourceId: string,
): Promise<{ task_id: string }> =>
  apiClient.post<{ task_id: string }>(
    `/api/v1/ai/analyze/resource/${resourceId}`,
  );

// --- Settings ---

interface RawAISettings {
  ai_enabled?: boolean;
  auto_transcribe?: boolean;
  auto_summarize?: boolean;
  preferred_language?: string;
  ai_providers?: AISettings['providers'];
  task_assignment?: Partial<AISettings['task_assignment']>;
}

export const getAISettings = async (): Promise<AISettings> => {
  const data = await apiClient.get<RawAISettings>('/api/v1/ai/settings');
  return {
    ai_enabled: data.ai_enabled ?? true,
    auto_transcribe: data.auto_transcribe ?? false,
    auto_summarize: data.auto_summarize ?? false,
    preferred_language: data.preferred_language ?? 'auto',
    providers: data.ai_providers ?? {},
    task_assignment: {
      transcription: data.task_assignment?.transcription ?? '',
      summarization: data.task_assignment?.summarization ?? '',
      visual_analysis: data.task_assignment?.visual_analysis ?? '',
      image_generation: data.task_assignment?.image_generation ?? '',
      script_generation: data.task_assignment?.script_generation ?? '',
    },
  } as AISettings;
};

export const saveAISettings = async (settings: AISettings): Promise<void> => {
  const transcription = settings.task_assignment?.transcription ?? '';
  const backendPayload = {
    ai_providers: settings.providers,
    whisper_provider: transcription.startsWith('volcengine')
      ? 'volcengine'
      : transcription.includes('openai')
        ? 'openai_api'
        : 'local',
    default_summary_model:
      settings.task_assignment?.summarization || 'gpt-4o-mini',
    default_analysis_model:
      settings.task_assignment?.visual_analysis || 'gpt-4o',
    ai_enabled: settings.ai_enabled,
    auto_transcribe: settings.auto_transcribe,
    auto_summarize: settings.auto_summarize,
    preferred_language: settings.preferred_language,
    task_assignment: settings.task_assignment,
  };

  await apiClient.put('/api/v1/ai/settings', backendPayload);
};

// --- Nous Models ---

export const getNousModels = async (
  category?: string,
): Promise<NousModelPublic[]> => {
  try {
    const data = await apiClient.get<{ models?: NousModelPublic[] }>(
      '/api/v1/ai/nous-models',
      { query: { category } },
    );
    return data.models || [];
  } catch {
    return [];
  }
};

export const testAIConnection = async (
  provider: string,
  config: { base_url?: string; api_key?: string; app_id?: string },
): Promise<{ success: boolean; models?: string[]; error?: string }> =>
  apiClient.post<{ success: boolean; models?: string[]; error?: string }>(
    '/api/v1/ai/test-connection',
    { provider_key: provider, ...config },
  );
