/**
 * aiService — media-AI operations (transcribe / summarize / visualize) +
 * AI provider settings.
 *
 * Chat / agent CRUD / session management used to live here too; they
 * were unified into the AI Library framework in U1/U2 (#65, #66). For
 * chat-related API calls use ``services/aiLibraryService.ts`` instead.
 */

import { AISettings, AIProviderConfig, TranscriptData, SummaryData } from '../types';
import type {
  AIGovernanceFlags,
  AnalyzeTriggerResponse,
  BackfillResult,
  CapabilityHealth,
  LegacySummarizeTriggerResponse,
  LegacyTranscribeTriggerResponse,
  NousModelPublic,
  SummarizeTriggerResponse,
  TranscribeTriggerResponse,
} from '../types/api';
import { getAuthHeaders } from './parserService';
import { getApiUrl } from '../utils/apiConfig';
import { apiClient } from './apiClient';

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
): Promise<LegacyTranscribeTriggerResponse> => {
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
  resourceId: string,
  opts?: { followUpSummary?: boolean }
): Promise<TranscribeTriggerResponse> => {
  const apiUrl = getApiUrl();

  // followUpSummary persists the "summarize once this transcription
  // completes" intent SERVER-side (resources.summary_follow_up, 438) — the
  // in-memory registry below still runs as the fast path, but a page
  // refresh no longer loses the summary (PR #1927's documented boundary).
  const response = await fetch(`${apiUrl}/api/v1/ai/transcribe/resource/${resourceId}`, {
    method: 'POST',
    headers: { ...(await getAuthHeaders()), 'Content-Type': 'application/json' },
    ...(opts?.followUpSummary
      ? { body: JSON.stringify({ follow_up_summary: true }) }
      : {}),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
};

/** Sentinel thrown when backend says "no transcript yet" (200 + null
 * fields). pollForResult catches it and keeps polling; UI callers
 * catch it and render the "No Transcript Available" empty state. */
class TranscriptNotReadyError extends Error {
  readonly notReady = true;
  constructor() { super('Transcript not ready'); }
}

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
  if (!raw.full_text && !raw.text) throw new TranscriptNotReadyError();
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
  if (!raw.full_text && !raw.text) throw new TranscriptNotReadyError();
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
): Promise<LegacySummarizeTriggerResponse> => {
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
): Promise<SummarizeTriggerResponse> => {
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

/** Sentinel for "summary not ready yet" — same pattern as
 * TranscriptNotReadyError. Lets pollForResult continue and lets UI
 * callers render an empty state without console-error noise. */
class SummaryNotReadyError extends Error {
  readonly notReady = true;
  constructor() { super('Summary not ready'); }
}

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
  if (!raw.summary_text && !raw.summary) throw new SummaryNotReadyError();
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
  if (!raw.summary_text && !raw.summary) throw new SummaryNotReadyError();
  return {
    summary: raw.summary_text || raw.summary || '',
    key_points: raw.key_points || [],
    topics: raw.topics || [],
    created_at: raw.created_at || '',
  } as SummaryData;
};

// --- Visual Analysis ---

export const triggerVisualAnalysisByResource = async (
  resourceId: string
): Promise<AnalyzeTriggerResponse> => {
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

export interface VisualAnalysisData {
  description: string;
  objects: string[];
  scenes: string[];
  people: string[];
  text: string | null;
  model: string | null;
  cost: number | null;
}

/** Read the completed L1 visual analysis for a resource (vision result card). */
export const getVisualAnalysisByResource = async (
  resourceId: string
): Promise<VisualAnalysisData> => {
  const apiUrl = getApiUrl();

  const response = await fetch(`${apiUrl}/api/v1/ai/analysis/resource/${resourceId}`, {
    method: 'GET',
    headers: await getAuthHeaders(),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  const raw = await response.json();
  // Backend returns 200 + null fields when not analyzed yet — map to empty.
  return {
    description: raw.visual_description || '',
    objects: raw.detected_objects || [],
    scenes: raw.detected_scenes || [],
    people: raw.detected_people || [],
    text: raw.detected_text ?? null,
    model: raw.analysis_model ?? null,
    cost: raw.analysis_cost ?? null,
  };
};

// --- Capability health ---

export const getAIHealth = async (): Promise<CapabilityHealth[]> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/ai/health`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }
  const data: { capabilities?: CapabilityHealth[] } = await response.json();
  return data.capabilities ?? [];
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

  const data = await response.json();

  // Auto-seed enabled_models from selected_model for accounts that
  // predate the curated-whitelist feature. Without this, the agent
  // model picker would be empty until the user re-enters the settings
  // page and re-saves.
  const rawProviders = data.ai_providers ?? {};
  const providers: Record<string, AIProviderConfig> = {};
  for (const [key, raw] of Object.entries(rawProviders)) {
    const config = raw as AIProviderConfig;
    if (config.enabled_models == null && config.selected_model) {
      providers[key] = { ...config, enabled_models: [config.selected_model] };
    } else {
      providers[key] = config;
    }
  }

  // Map backend field names to frontend AISettings shape
  return {
    ai_enabled: data.ai_enabled ?? true,
    auto_transcribe: data.auto_transcribe ?? false,
    auto_summarize: data.auto_summarize ?? false,
    preferred_language: data.preferred_language ?? 'auto',
    transcription_hotwords: data.transcription_hotwords ?? '',
    providers,
    task_assignment: {
      transcription: data.task_assignment?.transcription ?? '',
      summarization: data.task_assignment?.summarization ?? '',
      visual_analysis: data.task_assignment?.visual_analysis ?? '',
      image_generation: data.task_assignment?.image_generation ?? '',
      script_generation: data.task_assignment?.script_generation ?? '',
    },
    // Persisted per-provider connection-test results (restores the Test
    // Connection status/detail across reloads).
    provider_health: data.provider_health ?? {},
  } as AISettings;
};

// GET-only masking metadata (secret-at-rest Phase 2) — never meaningful on
// the way back in, and echoing it verbatim would just persist stale noise
// into settings_json.ai_settings.ai_providers[provider] (the backend re-
// derives these from the real key on every GET/PUT response anyway).
const _READ_ONLY_PROVIDER_FIELDS = ['api_key_set', 'api_key_hint', 'api_key_count'] as const;

const stripReadOnlyProviderFields = (
  providers: AISettings['providers']
): AISettings['providers'] => {
  const out: AISettings['providers'] = {};
  for (const [key, config] of Object.entries(providers)) {
    if (!config) {
      out[key] = config;
      continue;
    }
    const clean = { ...config };
    for (const field of _READ_ONLY_PROVIDER_FIELDS) {
      delete clean[field];
    }
    out[key] = clean;
  }
  return out;
};

export const saveAISettings = async (
  settings: AISettings
): Promise<void> => {
  const apiUrl = getApiUrl();

  // Map frontend AISettings shape to backend AISettingsUpdate schema
  const backendPayload = {
    ai_providers: stripReadOnlyProviderFields(settings.providers),
    whisper_provider: settings.task_assignment?.transcription?.startsWith('volcengine') ? 'volcengine'
      : settings.task_assignment?.transcription?.includes('openai') ? 'openai_api' : 'local',
    // `default_summary_model` / `default_analysis_model` are deliberately NOT
    // sent: task_assignment.summarization / .visual_analysis hold AGENT SLUGS,
    // so this mapping was writing e.g. "summarize" into a field named for a
    // model. Nothing resolves either field any more (summarization was the
    // last reader and moved to the agent path on 2026-08-20), and posting a
    // slug as a model id would only re-create the confusion that cleanup
    // removed.
    // Include frontend-specific fields as extra data for persistence
    ai_enabled: settings.ai_enabled,
    auto_transcribe: settings.auto_transcribe,
    auto_summarize: settings.auto_summarize,
    preferred_language: settings.preferred_language,
    transcription_hotwords: settings.transcription_hotwords ?? '',
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

// --- Nous Models ---

export const getNousModels = async (type?: string): Promise<NousModelPublic[]> => {
  const apiUrl = getApiUrl();
  const params = type ? `?type=${type}` : '';
  const response = await fetch(`${apiUrl}/api/v1/ai/nous-models${params}`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) return [];
  const data: { models?: NousModelPublic[] } = await response.json();
  return data.models ?? [];
};

export const testAIConnection = async (
  provider: string,
  config: { base_url?: string; api_key?: string; app_id?: string }
): Promise<{
  success: boolean;
  models?: string[];
  error?: string;
  // Daily-quota counters surfaced by providers that expose them on
  // response headers (currently ModelScope).
  quota?: Record<string, number> | null;
}> => {
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

/**
 * Report a browser-direct local-provider (Ollama / LM Studio) connection-test
 * outcome so it persists across reloads. Cloud providers are persisted
 * server-side by /ai/test-connection and need no client report.
 *
 * Fire-and-forget: the caller does not await this; failures are best-effort
 * telemetry and must not affect the Test Connection UX.
 */
export const reportProviderHealth = async (
  providerKey: string,
  status: 'ok' | 'fail',
  detail = ''
): Promise<void> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/ai/provider-health`, {
    method: 'POST',
    headers: await getAuthHeaders(),
    body: JSON.stringify({ provider_key: providerKey, status, detail }),
  });
  if (!response.ok) {
    throw new Error(`POST /ai/provider-health failed: HTTP ${response.status}`);
  }
};

// --- AI Config Governance ---

/** Fail-open defaults: every module allowed when response is absent or errors. */
export const GOVERNANCE_ALL_ALLOWED: AIGovernanceFlags = {
  chat: true,
  transcription: true,
  translation: true,
  visual_analysis: true,
  caption: true,
  classification: true,
  summarization: true,
  topic_scorer: true,
  embedding: true,
  nous_enabled: false,
  nous_modules: {},
};

/**
 * Fetch per-module governance flags for the current user.
 *
 * Backend: GET /api/v1/ai/governance
 * Returns one bool per governed module (`AIGovernanceFlags`) plus the Nous
 * switches. true = user may configure; false = admin-managed (lock the UI row).
 * Throws on network/HTTP error — callers should default to GOVERNANCE_ALL_ALLOWED.
 */
export const getAIGovernance = async (): Promise<AIGovernanceFlags> => {
  const apiUrl = getApiUrl();
  const response = await fetch(`${apiUrl}/api/v1/ai/governance`, {
    headers: await getAuthHeaders(),
  });
  if (!response.ok) {
    throw new Error(`GET /ai/governance failed: HTTP ${response.status}`);
  }
  const data: AIGovernanceFlags = await response.json();
  // Merge with all-allowed defaults so any absent key stays true.
  return { ...GOVERNANCE_ALL_ALLOWED, ...data };
};

// --- Embedding backfill (Settings → AI → Vectors) ---

/** `POST /api/v1/ai/analyze/backfill-embeddings` (shape: `BackfillResult` in
 *  types/api). Refusals arrive as the ErrorResponse envelope with the typed
 *  code in `details.code` (`embedder_unconfigured` 409, `vector_store_missing`
 *  503). */
export const backfillEmbeddings = (body: {
  limit: number;
  dry_run: boolean;
  /** Fill this candidate space (Snowflake id as a string) with its own
   *  catalog model instead of the active embedder. */
  space_id?: string;
}): Promise<BackfillResult> =>
  apiClient.post<BackfillResult>('/api/v1/ai/analyze/backfill-embeddings', body);
