/**
 * Unit tests for aiService — pins URL/method/body shapes for the AI
 * endpoints after the apiClient migration, including the field-name
 * mapping in toTranscript/toSummary.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getAISettings,
  getNousModels,
  getSummary,
  getSummaryByResource,
  getTranscript,
  getTranscriptByResource,
  pollForResult,
  saveAISettings,
  testAIConnection,
  triggerSummary,
  triggerTranscription,
  triggerVisualAnalysis,
} from './aiService';

vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
vi.mock('../supabaseClient', () => ({
  getSupabaseAccessToken: vi.fn().mockResolvedValue(null),
}));

function stubJson(body: unknown, status: number = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
    ok: status < 400,
    status,
    headers: new Headers(),
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('transcription endpoints', () => {
  it('triggerTranscription POSTs /transcribe/:platformId', async () => {
    const spy = stubJson({ task_id: 't1' });
    await triggerTranscription('p-123');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/ai/transcribe/p-123',
    );
    expect((spy.mock.calls[0][1] as RequestInit).method).toBe('POST');
  });

  it('getTranscript maps full_text to text and duration_seconds to duration', async () => {
    stubJson({
      full_text: 'hello world',
      segments: [],
      language: 'en',
      duration_seconds: 42,
      created_at: '2026-04-17',
    });
    const result = await getTranscript('p-123');
    expect(result.text).toBe('hello world');
    expect(result.duration).toBe(42);
  });

  it('getTranscriptByResource falls back to text when full_text missing', async () => {
    stubJson({
      text: 'raw',
      segments: [],
      language: '',
      duration: 7,
      created_at: '',
    });
    const result = await getTranscriptByResource('r-1');
    expect(result.text).toBe('raw');
    expect(result.duration).toBe(7);
  });
});

describe('summary endpoints', () => {
  it('triggerSummary POSTs /summarize/:platformId', async () => {
    const spy = stubJson({ task_id: 's1' });
    await triggerSummary('p-123');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/ai/summarize/p-123',
    );
  });

  it('getSummary maps summary_text to summary', async () => {
    stubJson({
      summary_text: 'tldr',
      key_points: ['a', 'b'],
      topics: ['x'],
      created_at: '',
    });
    const result = await getSummary('p-123');
    expect(result.summary).toBe('tldr');
    expect(result.key_points).toEqual(['a', 'b']);
  });

  it('getSummaryByResource defaults missing arrays', async () => {
    stubJson({ summary: 'hi' });
    const result = await getSummaryByResource('r-1');
    expect(result.key_points).toEqual([]);
    expect(result.topics).toEqual([]);
  });
});

describe('visual analysis', () => {
  it('triggerVisualAnalysis POSTs /analyze/:platformId', async () => {
    const spy = stubJson({ task_id: 'v1' });
    await triggerVisualAnalysis('p-123');
    expect(spy.mock.calls[0][0]).toBe(
      'https://api.test/api/v1/ai/analyze/p-123',
    );
  });
});

describe('getAISettings', () => {
  it('maps ai_providers to providers with defaults', async () => {
    stubJson({
      ai_enabled: false,
      auto_transcribe: true,
      ai_providers: { openai: { enabled: true } },
      task_assignment: {
        transcription: 'whisper',
        summarization: 'gpt-4o',
      },
    });

    const settings = await getAISettings();
    expect(settings.ai_enabled).toBe(false);
    expect(settings.auto_transcribe).toBe(true);
    expect(settings.providers).toEqual({ openai: { enabled: true } });
    expect(settings.task_assignment.transcription).toBe('whisper');
  });

  it('applies defaults when backend returns minimal data', async () => {
    stubJson({});
    const settings = await getAISettings();
    expect(settings.ai_enabled).toBe(true);
    expect(settings.preferred_language).toBe('auto');
    expect(settings.task_assignment.transcription).toBe('');
  });

  it('auto-seeds enabled_models from selected_model for legacy accounts', async () => {
    // Pre-feature accounts only had `selected_model`. The agent picker
    // would render empty without a seed, so getAISettings backfills it
    // on read.
    stubJson({
      ai_providers: {
        doubao: {
          enabled: true,
          api_key: 'sk-test',
          selected_model: 'doubao-seed-2-0-pro-260215',
        },
      },
    });

    const settings = await getAISettings();
    expect(settings.providers.doubao?.enabled_models).toEqual([
      'doubao-seed-2-0-pro-260215',
    ]);
  });

  it('preserves existing enabled_models without overwriting', async () => {
    stubJson({
      ai_providers: {
        doubao: {
          enabled: true,
          selected_model: 'doubao-seed-2-0-pro-260215',
          enabled_models: ['doubao-seed-2-0-pro-260215', 'doubao-seed-2-0-lite-260215'],
        },
      },
    });

    const settings = await getAISettings();
    expect(settings.providers.doubao?.enabled_models).toEqual([
      'doubao-seed-2-0-pro-260215',
      'doubao-seed-2-0-lite-260215',
    ]);
  });

  it('leaves enabled_models undefined when there is no selected_model to seed from', async () => {
    // A provider that's never been touched has neither field; the
    // picker treats undefined as "nothing yet" and renders only the
    // "+ Add Model" button.
    stubJson({ ai_providers: { kimi: { enabled: false } } });
    const settings = await getAISettings();
    expect(settings.providers.kimi?.enabled_models).toBeUndefined();
  });
});

describe('saveAISettings', () => {
  it('chooses volcengine provider for volcengine-prefixed transcription', async () => {
    const spy = stubJson({ success: true });
    await saveAISettings({
      ai_enabled: true,
      auto_transcribe: false,
      auto_summarize: false,
      preferred_language: 'auto',
      providers: {},
      task_assignment: {
        transcription: 'volcengine-16k',
        summarization: '',
        visual_analysis: '',
        image_generation: '',
        script_generation: '',
      },
    });
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.whisper_provider).toBe('volcengine');
  });

  it('chooses openai_api when transcription contains openai', async () => {
    const spy = stubJson({ success: true });
    await saveAISettings({
      ai_enabled: true,
      auto_transcribe: false,
      auto_summarize: false,
      preferred_language: 'auto',
      providers: {},
      task_assignment: {
        transcription: 'openai-whisper',
        summarization: '',
        visual_analysis: '',
        image_generation: '',
        script_generation: '',
      },
    });
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.whisper_provider).toBe('openai_api');
  });

  it('defaults to local provider otherwise', async () => {
    const spy = stubJson({ success: true });
    await saveAISettings({
      ai_enabled: true,
      auto_transcribe: false,
      auto_summarize: false,
      preferred_language: 'auto',
      providers: {},
      task_assignment: {
        transcription: '',
        summarization: '',
        visual_analysis: '',
        image_generation: '',
        script_generation: '',
      },
    });
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.whisper_provider).toBe('local');
  });
});

describe('getNousModels', () => {
  it('threads category query', async () => {
    const spy = stubJson({ models: [{ name: 'nous-x' }] });
    await getNousModels('transcription');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('category=transcription');
  });

  it('returns [] on error', async () => {
    stubJson({ detail: 'oops' }, 500);
    const result = await getNousModels();
    expect(result).toEqual([]);
  });

  it('returns [] when models field missing', async () => {
    stubJson({});
    const result = await getNousModels();
    expect(result).toEqual([]);
  });
});

describe('testAIConnection', () => {
  it('POSTs provider_key + extra config', async () => {
    const spy = stubJson({ success: true, models: ['gpt-4o'] });
    await testAIConnection('openai', {
      base_url: 'https://api.openai.com',
      api_key: 'sk-xxx',
    });
    const body = JSON.parse(
      (spy.mock.calls[0][1] as RequestInit).body as string,
    );
    expect(body.provider_key).toBe('openai');
    expect(body.base_url).toBe('https://api.openai.com');
    expect(body.api_key).toBe('sk-xxx');
  });
});

describe('pollForResult', () => {
  it('returns when fetcher succeeds on first try', async () => {
    const result = await pollForResult(async () => 'ok', 1, 3);
    expect(result).toBe('ok');
  });

  it('throws immediately on 401 error', async () => {
    let attempts = 0;
    const fetcher = async () => {
      attempts++;
      throw new Error('HTTP 401 unauthorized');
    };
    await expect(pollForResult(fetcher, 1, 5)).rejects.toThrow('401');
    // Should exit on first error, not retry
    expect(attempts).toBe(1);
  });

  it('retries on transient errors until max', async () => {
    let attempts = 0;
    const fetcher = async () => {
      attempts++;
      throw new Error('not ready yet');
    };
    await expect(pollForResult(fetcher, 1, 3)).rejects.toThrow();
    expect(attempts).toBe(3);
  });
});
