/**
 * Floating save button (2026-09-23): the block "Save Settings" button became a
 * single icon button pinned to the bottom-right of the settings scroll area.
 * Pins: found by aria-label, unsaved-changes dot appears on edit and clears
 * on save, Ctrl/Cmd+S saves (and suppresses the browser's Save Page), and a
 * failed save surfaces the error next to the button instead of vanishing.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent, act } from '@testing-library/react';
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

const saveButton = () => screen.getByRole('button', { name: 'Save Settings' });
const unsavedDot = () => screen.queryByTestId('ai-settings-unsaved-dot');

async function renderWithModels(onSave = vi.fn()) {
  const { getNousModels } = await import('../services/aiService');
  vi.mocked(getNousModels).mockResolvedValue(MODELS);
  render(<AISettings settings={baseSettings} onSave={onSave} />);
  await waitFor(() => {
    expect(screen.getByText('MOSS ASR')).toBeInTheDocument();
  });
  return onSave;
}

describe('AISettings — floating save button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('is an icon-only button, enabled even with no pending edits', async () => {
    await renderWithModels();
    const btn = saveButton();
    expect(btn).toBeEnabled();
    expect(btn).not.toHaveTextContent('Save Settings');
    expect(btn.getAttribute('title')).toMatch(/^Save Settings \((⌘S|Ctrl\+S)\)$/);
    expect(unsavedDot()).toBeNull();
  });

  it('shows the unsaved dot after an edit and clears it once saved', async () => {
    const onSave = await renderWithModels();
    fireEvent.click(modelToggle('MOSS ASR'));
    expect(unsavedDot()).toBeInTheDocument();
    expect(unsavedDot()).toHaveTextContent('Unsaved changes');

    fireEvent.click(saveButton());

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(unsavedDot()).toBeNull());
    // Success state swaps the accessible name to the confirmation copy.
    expect(screen.getByRole('button', { name: 'Settings Saved!' })).toBeInTheDocument();
  });

  it('Ctrl+S saves and prevents the browser default', async () => {
    const { saveAISettings } = await import('../services/aiService');
    const onSave = await renderWithModels();
    fireEvent.click(modelToggle('MOSS ASR'));

    const event = new KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true, cancelable: true });
    await act(async () => {
      document.dispatchEvent(event);
    });

    expect(event.defaultPrevented).toBe(true);
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(saveAISettings).toHaveBeenCalledTimes(1);
  });

  it('Cmd+S saves too; a plain "s" keystroke does not', async () => {
    const { saveAISettings } = await import('../services/aiService');
    await renderWithModels();

    fireEvent.keyDown(document, { key: 's' });
    expect(saveAISettings).not.toHaveBeenCalled();

    fireEvent.keyDown(document, { key: 's', metaKey: true });
    await waitFor(() => expect(saveAISettings).toHaveBeenCalledTimes(1));
  });

  it('a failed save shows the error beside the button, logs it, and keeps the dot', async () => {
    const { saveAISettings } = await import('../services/aiService');
    vi.mocked(saveAISettings).mockRejectedValueOnce(new Error('HTTP 500'));
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const onSave = await renderWithModels();
    fireEvent.click(modelToggle('MOSS ASR'));

    fireEvent.click(saveButton());

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('HTTP 500');
    expect(errSpy).toHaveBeenCalledWith('[AISettings] save failed:', expect.any(Error));
    expect(onSave).not.toHaveBeenCalled();
    expect(unsavedDot()).toBeInTheDocument();
    errSpy.mockRestore();
  });
});
