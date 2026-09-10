// The Local codex pairing card lives INSIDE the OpenAI provider card (user
// decision 2026-08-25) — pin that an enabled OpenAI card renders it, and that
// the retired per-task model selectors stay gone.
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import AISettings from './AISettings';
import type { AISettings as AISettingsType } from '../types';

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

vi.mock('../services/aiService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/aiService')>()),
  getNousModels: vi.fn().mockResolvedValue([]),
  getModuleGovernance: vi.fn().mockResolvedValue({
    chat: true, transcription: true, translation: true, visual_analysis: true,
    caption: true, classification: true, summarization: true,
  }),
  // The panel actually calls getAIGovernance, not getModuleGovernance. Left
  // unmocked it made a REAL request that settled after jsdom was torn down —
  // an EnvironmentTeardownError attributed to this file with nothing failing
  // in it (2026-09-10).
  getAIGovernance: vi.fn().mockResolvedValue({
    chat: true, transcription: true, translation: true, visual_analysis: true,
    caption: true, classification: true, summarization: true,
    nous_enabled: false, nous_modules: {},
  }),
}));
vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: { listAgents: vi.fn().mockResolvedValue([]) },
}));
vi.mock('../services/codexDaemonService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/codexDaemonService')>();
  return {
    ...actual,
    codexDaemonService: {
      createPairCode: vi.fn(),
      listDevices: vi.fn().mockResolvedValue([]),
      revokeDevice: vi.fn(),
    },
  };
});
vi.mock('./MCPServersPanel', () => ({ MCPServersPanel: () => null }));
vi.mock('./ApprovalsPanel', () => ({ ApprovalsPanel: () => null }));
vi.mock('./MemoryPanel', () => ({ MemoryPanel: () => null }));
vi.mock('./AIHealthBoard', () => ({ AIHealthBoard: () => null }));
vi.mock('../features/canvas-core/smart/NousCenterVerifyPanel', () => ({
  NousCenterVerifyPanel: () => null,
}));

const settings: AISettingsType = {
  ai_enabled: true,
  auto_transcribe: false,
  auto_summarize: false,
  preferred_language: 'auto',
  providers: { openai: { enabled: true, api_key: '' } },
  task_assignment: {
    transcription: '', summarization: '', visual_analysis: '', translation: '',
    caption: '', classification: '', image_generation: '', script_generation: '',
  },
} as AISettingsType;

describe('settings tab split (2026-08-26, sub-tabs inside AI)', () => {
  it('the AI area shows sub-tabs and Local CLI renders the pairing card', async () => {
    const { AISettingsTabs } = await import('./settings/AISettingsTabs');
    const { fireEvent } = await import('@testing-library/react');
    render(<AISettingsTabs settings={settings} onSave={vi.fn()} />);
    expect(screen.getByTestId('ai-subtabs')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('ai-subtab-local-cli'));
    await waitFor(() =>
      expect(screen.getByTestId('codex-daemon-settings')).toBeInTheDocument(),
    );
  });

  it('the providers section has neither the daemon card nor retired selectors', async () => {
    render(<AISettings settings={settings} onSave={vi.fn()} section="providers" />);
    await waitFor(() => expect(screen.getByText('AI Providers')).toBeInTheDocument());
    expect(screen.queryByTestId('codex-daemon-settings')).toBeNull();
    expect(screen.queryByText('Whisper Model')).toBeNull();
    expect(screen.queryByText('Summary Model')).toBeNull();
    expect(screen.queryByText('Analysis Model')).toBeNull();
  });
});
