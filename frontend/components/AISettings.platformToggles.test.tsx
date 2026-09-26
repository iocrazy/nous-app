/**
 * Tests for the "Nous (Platform)" card user-side visibility controls:
 *   1. Master toggle off → platform models vanish from every picker AND the
 *      card body collapses to header-only.
 *   2. Per-model blacklist (`disabled_models`), edited through the same
 *      "Enabled Models" chips + "Add Model" picker as every BYOK card:
 *      removing a chip opts the row out; a disabled row is not a chip but is
 *      offered by Add Model, and picking it there opts it back in. Only the
 *      opted-out model disappears from the pickers; the rest stay.
 *   3. Default (nothing blacklisted) → all platform models visible.
 *   4. Save payload carries providers.nous and does NOT clobber other
 *      providers (merge-safe shape).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType } from '../types';
import { baseAISettings, withPlatform, type PlatformRowSpec } from '../tests/fixtures/platform';

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

// moss-asr has no actual_model, so it reads as its row name; nous-llm reads
// as its actual_model. Either way display_name is never shown.
const MODELS: PlatformRowSpec[] = [
  { name: 'moss-asr', actual_model: '', type: 'asr', pricing_type: 'per_hour', pricing_value: 3 },
  { name: 'nous-llm', actual_model: 'qwen3-8b-instruct', type: 'llm', pricing_type: 'per_token', pricing_value: 2 },
];

// ASR picker label (transcription <select>) and LLM picker label (agent <select>),
// both named by the admin identifier.
const ASR_OPTION = /moss-asr \(Platform/;
const LLM_OPTION = /qwen3-8b-instruct \(Platform\)/;

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn(async (s: unknown) => s), // PUT echoes the saved settings
  testAIConnection: vi.fn(),
  getPlatformStatus: vi.fn(() => new Promise(() => {})),
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

interface RenderOptions {
  /** The stored master switch. */
  enabled?: boolean;
  /** Row names in the stored blacklist. */
  disabled?: string[];
  providers?: AISettingsType['providers'];
}

/** Settings as GET /ai/settings returns them: the server-computed platform
 *  card (models / enabled_models / disabled_models) plus the mapping. */
function renderSettings({ enabled = true, disabled = [], providers = {} }: RenderOptions = {}) {
  const rows = MODELS.map((m) => ({ ...m, disabled: disabled.includes(m.name) }));
  const settings = withPlatform(baseAISettings({ providers }), rows, { enabled });
  return render(<AISettings settings={settings} onSave={vi.fn()} />);
}

/** The platform card (header + body). */
function platformCard(): HTMLElement {
  const card = screen.getByText('Nous (Platform)').closest('.rounded-xl');
  if (!card) throw new Error('platform card not found');
  return card as HTMLElement;
}

/** The chip for one platform row, keyed by the row `name`. */
function chip(name: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(
    `[data-testid="platform-model-row"][data-model-name="${name}"]`,
  );
}

function chipNames(): Array<string | null> {
  return screen
    .queryAllByTestId('platform-model-row')
    .map((el) => el.getAttribute('data-model-name'));
}

async function saveAndGetPayload(): Promise<AISettingsType> {
  const { saveAISettings } = await import('../services/aiService');
  fireEvent.click(screen.getByRole('button', { name: 'Save Settings' }));
  await waitFor(() => {
    expect(saveAISettings).toHaveBeenCalledTimes(1);
  });
  return vi.mocked(saveAISettings).mock.calls[0][0] as AISettingsType;
}

describe('AISettings — platform card master toggle + per-model chips', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('default (nothing blacklisted): all platform models visible in pickers', async () => {
    renderSettings();

    // UiSelect mirrors each option into a native + a custom-rendered list, so
    // an option label appears more than once — assert presence via getAllByText.
    await waitFor(() => {
      expect(screen.getAllByText(ASR_OPTION).length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText(LLM_OPTION).length).toBeGreaterThan(0);
    // Card body renders both rows as enabled chips, named by admin identifier.
    expect(chipNames()).toEqual(['nous-llm', 'moss-asr']);
    expect(within(chip('nous-llm')!).getByText('qwen3-8b-instruct')).toBeInTheDocument();
    expect(within(chip('moss-asr')!).getByText('moss-asr')).toBeInTheDocument();
  });

  it('master toggle OFF (stored): pickers drop platform models AND card body collapses', async () => {
    renderSettings({ enabled: false });

    // Card header still present…
    await waitFor(() => {
      expect(screen.getByText('Nous (Platform)')).toBeInTheDocument();
    });
    // …but body collapsed: no chips, no Add Model, and pickers show no
    // platform options.
    expect(screen.queryAllByTestId('platform-model-row')).toHaveLength(0);
    expect(within(platformCard()).queryByRole('button', { name: 'Add Model' })).toBeNull();
    expect(screen.queryAllByText(ASR_OPTION)).toHaveLength(0);
    expect(screen.queryAllByText(LLM_OPTION)).toHaveLength(0);
  });

  it('per-model blacklist: only the opted-out model leaves the picker', async () => {
    renderSettings({ disabled: ['moss-asr'] });

    await waitFor(() => {
      // LLM still selectable…
      expect(screen.getAllByText(LLM_OPTION).length).toBeGreaterThan(0);
    });
    // …ASR gone from the picker and from the chips — but still offered by the
    // card's Add Model picker so the user can re-enable it.
    expect(screen.queryAllByText(ASR_OPTION)).toHaveLength(0);
    expect(chipNames()).toEqual(['nous-llm']);
    fireEvent.click(within(platformCard()).getByRole('button', { name: 'Add Model' }));
    const candidate = within(platformCard()).getByRole('button', { name: /^moss-asr/ });
    expect(within(candidate).getByText('ASR')).toHaveAttribute('title', 'ASR');
    // The enabled row is not a candidate.
    expect(within(platformCard()).queryByRole('button', { name: /^qwen3-8b-instruct/ })).toBeNull();
  });

  it('removing a chip then Save persists nous.disabled_models without clobbering other providers', async () => {
    renderSettings({
      providers: {
        openai: { enabled: true, api_key_set: true, selected_model: 'gpt-4o' },
      },
    });

    await waitFor(() => expect(chip('moss-asr')).not.toBeNull());

    // Opt the ASR model out via its chip's ×, then save.
    fireEvent.click(within(chip('moss-asr')!).getByRole('button', { name: 'Remove moss-asr' }));
    expect(chip('moss-asr')).toBeNull();
    expect(chipNames()).toEqual(['nous-llm']);

    const saved = await saveAndGetPayload();
    // nous blacklist recorded by row name…
    expect(saved.providers.nous?.disabled_models).toEqual(['moss-asr']);
    expect(saved.providers.nous?.enabled).not.toBe(false);
    // …and the pre-existing provider is untouched (merge-safe).
    expect(saved.providers.openai?.enabled).toBe(true);
    expect(saved.providers.openai?.selected_model).toBe('gpt-4o');
  });

  it('removing a chip labelled by actual_model blacklists the row name, not the label', async () => {
    renderSettings();

    await waitFor(() => expect(chip('nous-llm')).not.toBeNull());
    fireEvent.click(
      within(chip('nous-llm')!).getByRole('button', { name: 'Remove qwen3-8b-instruct' }),
    );

    const saved = await saveAndGetPayload();
    expect(saved.providers.nous?.disabled_models).toEqual(['nous-llm']);
  });

  it('picking a disabled row from Add Model re-enables it and Save drops it from disabled_models', async () => {
    renderSettings({ disabled: ['moss-asr', 'nous-llm'] });

    await waitFor(() => {
      expect(within(platformCard()).getByRole('button', { name: 'Add Model' })).toBeInTheDocument();
    });
    expect(chipNames()).toEqual([]);

    fireEvent.click(within(platformCard()).getByRole('button', { name: 'Add Model' }));
    // The filter narrows by the visible admin identifier.
    fireEvent.change(within(platformCard()).getByPlaceholderText('Filter models...'), {
      target: { value: 'moss' },
    });
    expect(within(platformCard()).queryByRole('button', { name: /^qwen3-8b-instruct/ })).toBeNull();
    fireEvent.click(within(platformCard()).getByRole('button', { name: /^moss-asr/ }));

    // Back among the chips, and back in the transcription picker.
    expect(chipNames()).toEqual(['moss-asr']);
    expect(screen.getAllByText(ASR_OPTION).length).toBeGreaterThan(0);

    const saved = await saveAndGetPayload();
    expect(saved.providers.nous?.disabled_models).toEqual(['nous-llm']);
    expect(saved.providers.nous?.disabled_models).not.toContain('moss-asr');
  });

  it('clicking the master toggle OFF then Save persists nous.enabled=false', async () => {
    const { saveAISettings } = await import('../services/aiService');

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText('Nous (Platform)')).toBeInTheDocument();
    });

    // The master toggle is the button in the platform-card header row.
    const header = screen.getByText('Nous (Platform)').closest('.flex.items-center.gap-4');
    const masterToggle = within(header as HTMLElement).getByRole('button');
    fireEvent.click(masterToggle);
    fireEvent.click(screen.getByRole('button', { name: 'Save Settings' }));

    await waitFor(() => {
      expect(saveAISettings).toHaveBeenCalledTimes(1);
    });
    const saved = vi.mocked(saveAISettings).mock.calls[0][0] as AISettingsType;
    expect(saved.providers.nous?.enabled).toBe(false);
  });

  // The platform catalog IS the admin's list. On a BYOK card a typed name
  // outside the catalog can still be enabled (providers rename models faster
  // than any list keeps up); here it has nothing to enable — the only thing
  // "add" can do is drop a row name from the blacklist — so the button must
  // not be offered at all rather than sit there as a silent no-op.
  it('does not offer "Add <typed>" for a name outside the platform catalog', async () => {
    renderSettings({ disabled: ['moss-asr'] });

    await waitFor(() => {
      expect(within(platformCard()).getByRole('button', { name: 'Add Model' })).toBeInTheDocument();
    });
    fireEvent.click(within(platformCard()).getByRole('button', { name: 'Add Model' }));
    const filter = within(platformCard()).getByPlaceholderText(en.aiSettings.filterModels);
    fireEvent.change(filter, { target: { value: 'not-a-platform-row' } });

    expect(within(platformCard()).queryByTestId('add-custom-model')).toBeNull();
    expect(within(platformCard()).getByText(en.aiSettings.noMatches)).toBeInTheDocument();
  });

  // The PUT echo is the recomputed settings (spec 2026-09-25 §3.1): the page
  // hands THAT to the parent, not its own local copy, so `enabled_models`
  // reflects the saved blacklist without a second request.
  it('hands the PUT echo — not the local draft — to onSave', async () => {
    const { saveAISettings } = await import('../services/aiService');
    const echo = withPlatform(baseAISettings(), [
      { ...MODELS[0], disabled: true },
      MODELS[1],
    ]);
    vi.mocked(saveAISettings).mockResolvedValueOnce(echo);
    const onSave = vi.fn();
    render(<AISettings settings={withPlatform(baseAISettings(), MODELS)} onSave={onSave} />);

    await waitFor(() => expect(chip('moss-asr')).not.toBeNull());
    fireEvent.click(within(chip('moss-asr')!).getByRole('button', { name: 'Remove moss-asr' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Settings' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0][0]).toBe(echo);
    expect(onSave.mock.calls[0][0].providers.nous.enabled_models).toEqual(['nous-llm']);
  });
});
