/**
 * Unit tests for AgentEditor's pure helpers. The agent model picker
 * narrowed from "full provider catalog" (100+ Doubao entries) to a
 * curated whitelist; these pin the contract for that whitelist read.
 */

import { describe, expect, it } from 'vitest';
import { getAvailableModels } from './AgentEditor';
import type { AISettings } from '../../types';

function buildSettings(overrides: Partial<AISettings>): AISettings {
  return {
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
    ...overrides,
  };
}

describe('getAvailableModels', () => {
  it('returns empty when no providers are enabled', () => {
    const settings = buildSettings({
      providers: { doubao: { enabled: false, enabled_models: ['doubao-seed-2-0-pro-260215'] } },
    });
    expect(getAvailableModels(settings)).toEqual([]);
  });

  it('returns only enabled_models from enabled providers — never the full catalog', () => {
    const settings = buildSettings({
      providers: {
        doubao: {
          enabled: true,
          enabled_models: ['doubao-seed-2-0-pro-260215', 'doubao-seed-2-0-lite-260215'],
          // The full provider catalog (what Test Connection populated)
          // must NOT leak through into the agent picker — that's the
          // whole point of this feature.
          models: Array.from({ length: 100 }, (_, i) => `doubao-bulk-${i}`),
          selected_model: 'doubao-seed-2-0-pro-260215',
        },
      },
    });

    const groups = getAvailableModels(settings);
    expect(groups).toHaveLength(1);
    expect(groups[0].providerKey).toBe('doubao');
    expect(groups[0].models).toEqual([
      'doubao-seed-2-0-pro-260215',
      'doubao-seed-2-0-lite-260215',
    ]);
  });

  it('falls back to [selected_model] for in-flight migrations', () => {
    // If the backend returns a row that hasn't been auto-seeded yet
    // (e.g. legacy account loaded via a different code path), fall
    // back to the single selected_model rather than rendering empty.
    const settings = buildSettings({
      providers: {
        kimi: {
          enabled: true,
          selected_model: 'kimi-k2.5',
          // enabled_models intentionally omitted
        },
      },
    });
    expect(getAvailableModels(settings)[0].models).toEqual(['kimi-k2.5']);
  });

  it('hides providers with empty whitelist and no selected_model', () => {
    const settings = buildSettings({
      providers: { qwen: { enabled: true, enabled_models: [] } },
    });
    expect(getAvailableModels(settings)).toEqual([]);
  });

  it('de-duplicates models within a provider', () => {
    const settings = buildSettings({
      providers: {
        doubao: {
          enabled: true,
          enabled_models: ['doubao-pro', 'doubao-pro', 'doubao-lite'],
        },
      },
    });
    expect(getAvailableModels(settings)[0].models).toEqual(['doubao-pro', 'doubao-lite']);
  });
});
