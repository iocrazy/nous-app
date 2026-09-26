/**
 * Regression: task-assignment pickers must not flash a wrong value while the
 * async option sources (platform models / agents / governance) are still
 * loading.
 *
 * The bug: a user's stored value (e.g. transcription = "nous:mediahub-moss-asr")
 * isn't in the option list until the platform list arrives — since spec
 * 2026-09-25 that list rides on the AI settings, which AuthContext loads after
 * login, so the page can mount before `platform_models` is there. A native <select> —
 * and the custom UiSelect that mirrors it — falls back to the FIRST option when
 * the value doesn't match, so the picker briefly showed "Volcengine bigasr"
 * then snapped to the real value once data landed. The agent pickers similarly
 * flashed "No Agents Available" / a legacy state before listAgents resolved.
 *
 * The fix seeds an `optionsReady` gate: until all three sources settle, each
 * picker renders a disabled placeholder echoing the stored raw value.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType } from '../types';
import { withPlatform } from '../tests/fixtures/platform';

import en from '../public/locales/en.json';

// Resolve against the REAL shipped English copy rather than a hand-written
// table, so a missing/renamed key surfaces here as a failing assertion instead
// of a raw `aiSettings.someKey` reaching users. The component's own i18n
// instance is never initialized in tests (nothing loads i18n.ts), so
// react-i18next would otherwise hand back bare keys.
vi.mock('react-i18next', () => {
  // Created once by the factory so `t` is referentially stable across renders,
  // exactly like the real react-i18next hook. An unstable `t` would make any
  // hook that lists it as a dependency re-fire on every render.
  const t = (key: string, vars?: Record<string, unknown>): string => {
    const template = key
      .split('.')
      .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
    if (typeof template !== 'string') return key;
    return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars?.[name] ?? ''));
  };
  return { useTranslation: () => ({ t }) };
});

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn(async (s: unknown) => s), // PUT echoes the saved settings
  testAIConnection: vi.fn(),
  reportProviderHealth: vi.fn().mockResolvedValue(undefined),
  getPlatformStatus: vi.fn(() => new Promise(() => {})),
  getAIGovernance: vi.fn().mockResolvedValue({
    chat: true, transcription: true, translation: true,
    visual_analysis: true, caption: true, classification: true,
    summarization: true, nous_enabled: true, nous_modules: {},
  }),
  GOVERNANCE_ALL_ALLOWED: {
    chat: true, transcription: true, translation: true,
    visual_analysis: true, caption: true, classification: true,
    summarization: true, nous_enabled: false, nous_modules: {},
  },
}));
vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: { listAgents: vi.fn().mockResolvedValue([]) },
}));
vi.mock('./MCPServersPanel', () => ({ MCPServersPanel: () => null }));
vi.mock('./ApprovalsPanel', () => ({ ApprovalsPanel: () => null }));
vi.mock('./MemoryPanel', () => ({ MemoryPanel: () => null }));
vi.mock('./AgentMemoriesPanel', () => ({ AgentMemoriesPanel: () => null }));
vi.mock('./AIHealthBoard', () => ({ AIHealthBoard: () => null }));

// Volcengine enabled → its whisperModels seed the first static option
// ("Volcengine bigasr") the buggy picker used to flash.
const settingsWithNousAsr: AISettingsType = {
  ai_enabled: true,
  auto_transcribe: false,
  auto_summarize: false,
  preferred_language: 'auto',
  providers: {
    volcengine: { enabled: true, api_key: 'tok', app_id: 'app' },
  },
  task_assignment: {
    transcription: 'nous:mediahub-moss-asr',
    summarization: '',
    visual_analysis: '',
    image_generation: '',
    script_generation: '',
  },
};

describe('AISettings task-assignment loading states', () => {
  it('does not flash "Volcengine bigasr" while platform models load; shows the stored value, then the resolved model', async () => {
    // AuthContext has not loaded the settings yet: no `platform_models`.
    const { rerender } = render(<AISettings settings={settingsWithNousAsr} onSave={vi.fn()} />);

    // While the platform list is absent, optionsReady is false: the picker must
    // echo the stored raw value, NOT the first static option. (Text appears in
    // both the visible trigger and the aria-hidden native <select>.)
    expect((await screen.findAllByText('nous:mediahub-moss-asr')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Volcengine bigasr')).toBeNull();

    // Platform models arrive → picker swaps to the real option label, named by
    // the admin identifier (row name here: no actual_model). (Post-
    // resolve, "Volcengine bigasr" is a legitimate *available* option in the
    // list — the point is it was never shown as the selected value.)
    rerender(
      <AISettings
        settings={withPlatform(settingsWithNousAsr, [
          { name: 'mediahub-moss-asr', actual_model: '', type: 'asr', pricing_type: 'per_hour', pricing_value: 10 },
        ])}
        onSave={vi.fn()}
      />,
    );

    expect((await screen.findAllByText(/mediahub-moss-asr \(Platform/)).length).toBeGreaterThan(0);
  });
});
