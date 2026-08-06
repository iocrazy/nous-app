/**
 * Unit tests for AgentEditor's pure helpers. The agent model picker
 * narrowed from "full provider catalog" (100+ Doubao entries) to a
 * curated whitelist; these pin the contract for that whitelist read.
 */

import { describe, expect, it } from 'vitest';
import { getAvailableModels } from './agentEditorModel';
import { LEGACY_TAB_MAP, SUB_TABS, resolveSubTab } from './AgentEditor';
import type { AISettings } from '../../types';

function buildSettings(overrides: Partial<AISettings>): AISettings {
  return {
    ai_enabled: true,
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

describe('resolveSubTab', () => {
  it('accepts the five current tabs', () => {
    expect(resolveSubTab('workbench')).toBe('workbench');
    expect(resolveSubTab('persona')).toBe('persona');
    expect(resolveSubTab('permissions')).toBe('permissions');
    expect(resolveSubTab('cost')).toBe('cost');
    expect(resolveSubTab('profile')).toBe('profile');
  });

  it('maps every one of the old eight tabs somewhere', () => {
    // Bookmarks and older in-app links carry these; landing them all on the
    // default tab would silently lose the user's place.
    expect(['runs', 'routines'].map(resolveSubTab)).toEqual(['workbench', 'workbench']);
    expect(
      ['overview', 'files', 'skills'].map(resolveSubTab),
    ).toEqual(['persona', 'persona', 'persona']);
    expect(resolveSubTab('versions')).toBe('profile');
    // `permissions` left LEGACY_TAB_MAP when it became a real tab again —
    // resolveSubTab matches SUB_TABS first, so an old ?tab=permissions link
    // now lands on the permissions tab instead of being folded into persona.
    expect(Object.keys(LEGACY_TAB_MAP)).toHaveLength(7);
    expect(LEGACY_TAB_MAP.permissions).toBeUndefined();
  });

  it('re-points the old dashboard link at Cost, where its content went', () => {
    // `?tab=dashboard` was the 14-day charts + spend breakdown. That block
    // rode along into Profile during the B2 rebuild and now has its own tab,
    // so the legacy link should follow the CONTENT rather than keep pointing
    // at the workbench, which shares none of it.
    expect(resolveSubTab('dashboard')).toBe('cost');
  });

  it('orders the tabs as the questions are asked', () => {
    // What is it doing → who is it → who may talk to it → what does it cost
    // → its paperwork. Cost is its own step because Profile was answering two
    // unrelated questions (spend trend vs version history) in one scroll.
    expect(SUB_TABS).toEqual([
      'workbench',
      'persona',
      'permissions',
      'cost',
      'profile',
    ]);
  });

  it('falls back to the workbench for missing or unknown values', () => {
    expect(resolveSubTab(null)).toBe('workbench');
    expect(resolveSubTab(undefined)).toBe('workbench');
    expect(resolveSubTab('nonsense')).toBe('workbench');
  });
});
