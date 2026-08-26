// The Local codex pairing card lives INSIDE the OpenAI provider card (user
// decision 2026-08-25) — pin that an enabled OpenAI card renders it, and that
// the retired per-task model selectors stay gone.
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import AISettings from './AISettings';
import type { AISettings as AISettingsType } from '../types';

vi.mock('../services/aiService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/aiService')>()),
  getNousModels: vi.fn().mockResolvedValue([]),
  getModuleGovernance: vi.fn().mockResolvedValue({
    chat: true, transcription: true, translation: true, visual_analysis: true,
    caption: true, classification: true, summarization: true,
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

describe('OpenAI provider card', () => {
  it('renders the Local codex pairing card when enabled', async () => {
    render(<AISettings settings={settings} onSave={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId('codex-daemon-settings')).toBeInTheDocument(),
    );
  });

  it('no longer renders the retired per-task model selectors', async () => {
    render(<AISettings settings={settings} onSave={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByTestId('codex-daemon-settings')).toBeInTheDocument(),
    );
    expect(screen.queryByText('Whisper Model')).toBeNull();
    expect(screen.queryByText('Summary Model')).toBeNull();
    expect(screen.queryByText('Analysis Model')).toBeNull();
  });
});
