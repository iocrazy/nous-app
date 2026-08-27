/**
 * Tests for the "Nous (Platform)" card user-side visibility controls:
 *   1. Master toggle off → platform models vanish from every picker AND the
 *      card body collapses to header-only.
 *   2. Per-model toggle (blacklist / disabled_models) → only the opted-out
 *      model disappears from the pickers; the rest stay.
 *   3. Default (no stored nous config) → all platform models visible.
 *   4. Save payload carries providers.nous and does NOT clobber other
 *      providers (merge-safe shape).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType, NousModelPublic } from '../types';

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

const ASR_MODEL: NousModelPublic = {
  name: 'moss-asr',
  display_name: 'MOSS ASR',
  type: 'asr',
  pricing_type: 'per_hour',
  pricing_value: 3,
};
const LLM_MODEL: NousModelPublic = {
  name: 'nous-llm',
  display_name: 'Nous LLM',
  type: 'llm',
  pricing_type: 'per_token',
  pricing_value: 2,
};
const MODELS: NousModelPublic[] = [ASR_MODEL, LLM_MODEL];

// ASR picker label (transcription <select>) and LLM picker label (agent <select>).
const ASR_OPTION = /MOSS ASR \(Platform/;
const LLM_OPTION = /Nous LLM \(Platform\)/;

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn().mockResolvedValue(undefined),
  testAIConnection: vi.fn(),
  getNousModels: vi.fn().mockResolvedValue([]),
  getAIGovernance: vi.fn().mockResolvedValue({
    chat: true, transcription: true, translation: true,
    visual_analysis: true, caption: true, classification: true,
    summarization: true,
    nous_enabled: true,
  }),
  GOVERNANCE_ALL_ALLOWED: {
    chat: true, transcription: true, translation: true,
    visual_analysis: true, caption: true, classification: true,
    summarization: true,
  },
}));
vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: { listAgents: vi.fn().mockResolvedValue([]) },
}));
vi.mock('./MCPServersPanel', () => ({ MCPServersPanel: () => null }));
vi.mock('./ApprovalsPanel', () => ({ ApprovalsPanel: () => null }));
vi.mock('./MemoryPanel', () => ({ MemoryPanel: () => null }));
vi.mock('./AIHealthBoard', () => ({ AIHealthBoard: () => null }));
vi.mock('../features/canvas-core/smart/NousCenterVerifyPanel', () => ({
  NousCenterVerifyPanel: () => null,
}));

const baseSettings: AISettingsType = {
  ai_enabled: true,
  auto_transcribe: false,
  auto_summarize: false,
  preferred_language: 'auto',
  providers: {},
  task_assignment: {
    transcription: '',
    summarization: '',
    visual_analysis: '',
    translation: '',
    caption: '',
    classification: '',
    image_generation: '',
    script_generation: '',
  },
};

function renderSettings(overrides?: Partial<AISettingsType>) {
  return render(
    <AISettings settings={{ ...baseSettings, ...overrides }} onSave={vi.fn()} />,
  );
}

/** Locate the per-model row's toggle button in the platform card. */
function modelToggle(displayName: string): HTMLElement {
  const row = screen.getByText(displayName).closest('.justify-between');
  if (!row) throw new Error(`row not found for ${displayName}`);
  return within(row as HTMLElement).getByRole('button');
}

describe('AISettings — platform card master + per-model toggles', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('default (no stored nous config): all platform models visible in pickers', async () => {
    const { getNousModels } = await import('../services/aiService');
    vi.mocked(getNousModels).mockResolvedValue(MODELS);

    renderSettings();

    // UiSelect mirrors each option into a native + a custom-rendered list, so
    // an option label appears more than once — assert presence via getAllByText.
    await waitFor(() => {
      expect(screen.getAllByText(ASR_OPTION).length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText(LLM_OPTION).length).toBeGreaterThan(0);
    // Card body renders both model rows (display name only — unique).
    expect(screen.getByText('MOSS ASR')).toBeInTheDocument();
    expect(screen.getByText('Nous LLM')).toBeInTheDocument();
  });

  it('master toggle OFF (stored): pickers drop platform models AND card body collapses', async () => {
    const { getNousModels } = await import('../services/aiService');
    vi.mocked(getNousModels).mockResolvedValue(MODELS);

    renderSettings({ providers: { nous: { enabled: false } } });

    // Card header still present…
    await waitFor(() => {
      expect(screen.getByText('Nous (Platform)')).toBeInTheDocument();
    });
    // …but body collapsed: no model rows, and pickers show no platform options.
    expect(screen.queryByText('MOSS ASR')).toBeNull();
    expect(screen.queryByText('Nous LLM')).toBeNull();
    expect(screen.queryAllByText(ASR_OPTION)).toHaveLength(0);
    expect(screen.queryAllByText(LLM_OPTION)).toHaveLength(0);
  });

  it('per-model blacklist: only the opted-out model leaves the picker', async () => {
    const { getNousModels } = await import('../services/aiService');
    vi.mocked(getNousModels).mockResolvedValue(MODELS);

    renderSettings({
      providers: { nous: { enabled: true, disabled_models: ['moss-asr'] } },
    });

    await waitFor(() => {
      // LLM still selectable…
      expect(screen.getAllByText(LLM_OPTION).length).toBeGreaterThan(0);
    });
    // …ASR gone from the picker (but the row still shows in the card so the
    // user can re-enable it).
    expect(screen.queryAllByText(ASR_OPTION)).toHaveLength(0);
    expect(screen.getByText('MOSS ASR')).toBeInTheDocument();
  });

  it('clicking a per-model toggle then Save persists nous.disabled_models without clobbering other providers', async () => {
    const { getNousModels, saveAISettings } = await import('../services/aiService');
    vi.mocked(getNousModels).mockResolvedValue(MODELS);

    renderSettings({
      providers: {
        openai: { enabled: true, api_key_set: true, selected_model: 'gpt-4o' },
      },
    });

    await waitFor(() => {
      expect(screen.getByText('Nous LLM')).toBeInTheDocument();
    });

    // Opt the ASR model out, then save.
    fireEvent.click(modelToggle('MOSS ASR'));
    fireEvent.click(screen.getByText('Save Settings'));

    await waitFor(() => {
      expect(saveAISettings).toHaveBeenCalledTimes(1);
    });
    const saved = vi.mocked(saveAISettings).mock.calls[0][0] as AISettingsType;
    // nous blacklist recorded…
    expect(saved.providers.nous?.disabled_models).toEqual(['moss-asr']);
    expect(saved.providers.nous?.enabled).not.toBe(false);
    // …and the pre-existing provider is untouched (merge-safe).
    expect(saved.providers.openai?.enabled).toBe(true);
    expect(saved.providers.openai?.selected_model).toBe('gpt-4o');
  });

  it('clicking the master toggle OFF then Save persists nous.enabled=false', async () => {
    const { getNousModels, saveAISettings } = await import('../services/aiService');
    vi.mocked(getNousModels).mockResolvedValue(MODELS);

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText('Nous (Platform)')).toBeInTheDocument();
    });

    // The master toggle is the button in the platform-card header row.
    const header = screen.getByText('Nous (Platform)').closest('.flex.items-center.gap-4');
    const masterToggle = within(header as HTMLElement).getByRole('button');
    fireEvent.click(masterToggle);
    fireEvent.click(screen.getByText('Save Settings'));

    await waitFor(() => {
      expect(saveAISettings).toHaveBeenCalledTimes(1);
    });
    const saved = vi.mocked(saveAISettings).mock.calls[0][0] as AISettingsType;
    expect(saved.providers.nous?.enabled).toBe(false);
  });
});
