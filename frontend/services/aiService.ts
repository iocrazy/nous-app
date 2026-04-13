
import { AISettings, TranscriptData, SummaryData } from '../types';
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { unwrapResponse } from '../utils/apiHelpers';

// ─── Chat/Agent Types ─────────────────────────────────────────────────────────

export interface AIAgent {
  id: string;
  name: string;
  description?: string;
  system_prompt?: string;
  model?: string;
  project_id?: string;
  created_at?: string;
  updated_at?: string;
}

export interface AISession {
  id: string;
  title?: string;
  project_id?: string;
  context_type?: string;
  context_id?: string;
  created_at?: string;
  updated_at?: string;
}

export interface AIMessage {
  id: string;
  session_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  agent_id?: string;
  agent_name?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  created_at?: string;
}

export interface AISessionWithMessages extends AISession {
  messages: AIMessage[];
  total: number;
}

export interface AIChatResponse {
  message: AIMessage;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
  };
}

export interface AIUsageStat {
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_messages: number;
  daily: Array<{
    date: string;
    prompt_tokens: number;
    completion_tokens: number;
    message_count: number;
  }>;
}

// ─── Agents ───────────────────────────────────────────────────────────────────

export async function fetchAgents(projectId?: string): Promise<AIAgent[]> {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams();
  if (projectId) params.set('project_id', projectId);
  const query = params.toString() ? `?${params}` : '';
  const res = await fetch(`${getApiUrl()}/api/v1/ai/agents${query}`, { headers });
  return unwrapResponse<AIAgent[]>(res);
}

export async function createAgent(data: Partial<AIAgent>): Promise<AIAgent> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/ai/agents`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<AIAgent>(res);
}

export async function updateAgent(id: string, data: Partial<AIAgent>): Promise<AIAgent> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/ai/agents/${id}`, {
    method: 'PUT',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return unwrapResponse<AIAgent>(res);
}

export async function deleteAgent(id: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${getApiUrl()}/api/v1/ai/agents/${id}`, {
    method: 'DELETE',
    headers,
  });
}

// ─── Sessions ─────────────────────────────────────────────────────────────────

export async function createSession(data: {
  title?: string;
  projectId?: string;
  contextType?: string;
  contextId?: string;
}): Promise<AISession> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/ai/sessions`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title: data.title,
      project_id: data.projectId,
      context_type: data.contextType,
      context_id: data.contextId,
    }),
  });
  return unwrapResponse<AISession>(res);
}

export async function fetchSessions(projectId?: string): Promise<AISession[]> {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams();
  if (projectId) params.set('project_id', projectId);
  const query = params.toString() ? `?${params}` : '';
  const res = await fetch(`${getApiUrl()}/api/v1/ai/sessions${query}`, { headers });
  return unwrapResponse<AISession[]>(res);
}

export async function fetchSessionWithMessages(
  sessionId: string,
  limit = 50,
  offset = 0,
): Promise<AISessionWithMessages> {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  const res = await fetch(
    `${getApiUrl()}/api/v1/ai/sessions/${sessionId}?${params}`,
    { headers },
  );
  return unwrapResponse<AISessionWithMessages>(res);
}

export async function deleteSession(sessionId: string): Promise<void> {
  const headers = await getAuthHeaders();
  await fetch(`${getApiUrl()}/api/v1/ai/sessions/${sessionId}`, {
    method: 'DELETE',
    headers,
  });
}

// ─── Chat ─────────────────────────────────────────────────────────────────────

export async function sendMessage(
  sessionId: string,
  message: string,
  agentId?: string,
  context?: Record<string, unknown>,
): Promise<AIChatResponse> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/ai/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, agent_id: agentId, context }),
  });
  return unwrapResponse<AIChatResponse>(res);
}

export function streamMessage(
  sessionId: string,
  message: string,
  agentId: string | undefined,
  onChunk: (text: string) => void,
  onDone: (usage: { prompt_tokens: number; completion_tokens: number }) => void,
  onError: (error: string) => void,
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const headers = await getAuthHeaders();
      const res = await fetch(
        `${getApiUrl()}/api/v1/ai/sessions/${sessionId}/chat/stream`,
        {
          method: 'POST',
          headers: { ...headers, 'Content-Type': 'application/json' },
          body: JSON.stringify({ message, agent_id: agentId }),
          signal: controller.signal,
        },
      );

      if (!res.ok) {
        onError(`Request failed: ${res.status} ${res.statusText}`);
        return;
      }

      const reader = res.body?.getReader();
      if (!reader) {
        onError('Response body is not readable');
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;

          const jsonStr = trimmed.slice(5).trim();
          if (!jsonStr || jsonStr === '[DONE]') continue;

          try {
            const parsed = JSON.parse(jsonStr) as {
              content?: string;
              done?: boolean;
              usage?: { prompt_tokens: number; completion_tokens: number };
              error?: string;
            };

            if (parsed.error) {
              onError(parsed.error);
              return;
            }

            if (parsed.done) {
              onDone(parsed.usage ?? { prompt_tokens: 0, completion_tokens: 0 });
              return;
            }

            if (parsed.content) {
              onChunk(parsed.content);
            }
          } catch {
            // Malformed SSE chunk — skip silently
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      onError((err as Error).message ?? 'Unknown streaming error');
    }
  })();

  return controller;
}

// ─── Agent Call ───────────────────────────────────────────────────────────────

export async function callAgent(
  agentId: string,
  action: string,
  context: Record<string, unknown>,
  projectId?: string,
): Promise<unknown> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/ai/agents/${agentId}/call`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, context, project_id: projectId }),
  });
  return unwrapResponse<unknown>(res);
}

// ─── Usage ────────────────────────────────────────────────────────────────────

export async function fetchUsage(projectId?: string, days = 30): Promise<AIUsageStat> {
  const headers = await getAuthHeaders();
  const params = new URLSearchParams({ days: String(days) });
  if (projectId) params.set('project_id', projectId);
  const res = await fetch(`${getApiUrl()}/api/v1/ai/usage?${params}`, { headers });
  return unwrapResponse<AIUsageStat>(res);
}

// ─── Legacy imports (kept for existing code) ──────────────────────────────────

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

// --- Transcription ---

/** @deprecated Use triggerTranscriptionByResource instead */
export const triggerTranscription = async (
  platformId: string
): Promise<{ task_id: string }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/transcribe/${platformId}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

export const triggerTranscriptionByResource = async (
  resourceId: string
): Promise<{ task_id: string }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/transcribe/resource/${resourceId}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/** @deprecated Use getTranscriptByResource instead */
export const getTranscript = async (
  platformId: string
): Promise<TranscriptData> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/transcript/${platformId}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
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

export const getTranscriptByResource = async (
  resourceId: string
): Promise<TranscriptData> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/transcript/resource/${resourceId}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const raw = await response.json();
  return {
    text: raw.full_text || raw.text || '',
    segments: raw.segments || [],
    language: raw.language || '',
    duration: raw.duration_seconds ?? raw.duration ?? 0,
    created_at: raw.created_at || '',
  } as TranscriptData;
};

// --- Summary ---

/** @deprecated Use triggerSummaryByResource instead */
export const triggerSummary = async (
  platformId: string
): Promise<{ task_id: string }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/summarize/${platformId}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

export const triggerSummaryByResource = async (
  resourceId: string
): Promise<{ task_id: string }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/summarize/resource/${resourceId}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/** @deprecated Use getSummaryByResource instead */
export const getSummary = async (
  platformId: string
): Promise<SummaryData> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/summary/${platformId}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
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

export const getSummaryByResource = async (
  resourceId: string
): Promise<SummaryData> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/summary/resource/${resourceId}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const raw = await response.json();
  return {
    summary: raw.summary_text || raw.summary || '',
    key_points: raw.key_points || [],
    topics: raw.topics || [],
    created_at: raw.created_at || '',
  } as SummaryData;
};

// --- Visual Analysis ---

/** @deprecated Use triggerVisualAnalysisByResource instead */
export const triggerVisualAnalysis = async (
  platformId: string
): Promise<{ task_id: string }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/analyze/${platformId}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

export const triggerVisualAnalysisByResource = async (
  resourceId: string
): Promise<{ task_id: string }> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/analyze/resource/${resourceId}`, {
    method: 'POST',
    headers: await getAuthHeaders(),
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
    headers: await getAuthHeaders(),
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
    headers: await getAuthHeaders(),
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
    headers: await getAuthHeaders(),
    body: JSON.stringify({ provider_key: provider, ...config }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};
