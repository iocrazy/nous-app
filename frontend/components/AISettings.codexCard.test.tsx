// Codex as a STANDARD provider card (user: "要像 Doubao 这样能够配置，配置完才会在
// 「通用」这里有个接入 AI 集合", 2026-09-05). The card has no API key — the
// paired daemon is the credential — so Test Connection must be reachable
// without one, and its models must reach the agent picker like any other
// provider's enabled models.
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import AISettings from './AISettings';
import { getAvailableModels } from './AILibrary/agentEditorModel';
import type { AISettings as AISettingsType } from '../types';

import en from '../public/locales/en.json';

vi.mock('react-i18next', () => {
  const t = (key: string, opts?: Record<string, unknown>) => {
    const raw = key.split('.').reduce<unknown>((o, k) => (o as Record<string, unknown>)?.[k], en);
    let s = typeof raw === 'string' ? raw : key;
    for (const [k, v] of Object.entries(opts ?? {})) s = s.replace(`{{${k}}}`, String(v));
    return s;
  };
  return { useTranslation: () => ({ t }) };
});

const testAIConnection = vi.fn();
vi.mock('../services/aiService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/aiService')>()),
  testAIConnection: (...a: unknown[]) => testAIConnection(...a),
  reportProviderHealth: vi.fn().mockResolvedValue(undefined),
  getNousModels: vi.fn().mockResolvedValue([]),
  getAIGovernance: vi.fn().mockResolvedValue({
    chat: true, transcription: true, translation: true, visual_analysis: true,
    caption: true, classification: true, summarization: true,
  }),
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

const base = (providers: AISettingsType['providers']): AISettingsType =>
  ({
    ai_enabled: true,
    auto_transcribe: false,
    auto_summarize: false,
    preferred_language: 'auto',
    providers,
    task_assignment: {
      transcription: '', summarization: '', visual_analysis: '', translation: '',
      caption: '', classification: '', image_generation: '', script_generation: '',
    },
  }) as AISettingsType;

describe('Codex provider card', () => {
  it('renders without an API key field and lets the user Test Connection anyway', async () => {
    render(<AISettings settings={base({ 'codex-local': { enabled: true } })} onSave={vi.fn()} section="providers" />);
    const card = await screen.findByTestId('provider-card-codex-local');
    expect(card.querySelector('input[type="password"]')).toBeNull();
    expect(card.textContent).not.toContain('API Key');
    expect(card.querySelector('[data-testid="test-connection-codex-local"]')).not.toBeNull();
  });

  it('Test Connection goes through the backend and fills the catalog with codex: ids', async () => {
    testAIConnection.mockResolvedValue({ success: true, models: ['codex:gpt-6-astra', 'codex:gpt-5.5'], error: null });
    render(<AISettings settings={base({ 'codex-local': { enabled: true } })} onSave={vi.fn()} section="providers" />);
    fireEvent.click(await screen.findByTestId('test-connection-codex-local'));
    await waitFor(() => expect(testAIConnection).toHaveBeenCalledWith('codex-local', expect.anything()));
    await screen.findByText('Connected');
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));
    expect(await screen.findByText('codex:gpt-6-astra')).toBeInTheDocument();
  });

  it('a name the catalog does not list can still be added by typing it', async () => {
    render(<AISettings settings={base({ 'codex-local': { enabled: true, models: ['codex:gpt-6-astra'] } })} onSave={vi.fn()} section="providers" />);
    await screen.findByTestId('provider-card-codex-local');
    fireEvent.click(screen.getByRole('button', { name: 'Add Model' }));
    fireEvent.change(screen.getByPlaceholderText('Filter models...'), { target: { value: 'codex:gpt-7' } });
    fireEvent.click(await screen.findByTestId('add-custom-model'));
    expect(screen.getByLabelText('Remove codex:gpt-7')).toBeInTheDocument();
  });
});

describe('Codex provider card — image model note', () => {
  it('says images use gpt-image-2 orchestrated by the first enabled model (user: "没有识别到图像模型?")', async () => {
    render(<AISettings settings={base({ 'codex-local': { enabled: true, enabled_models: ['codex:gpt-6-astra'] } })} onSave={vi.fn()} section="providers" />);
    const note = await screen.findByTestId('provider-note-codex-local');
    expect(note.textContent).toContain('gpt-image-2');
    expect(note.textContent).toContain('codex:gpt-6-astra');
  });
});

describe('agent model picker', () => {
  it('lists the Codex card under its own group once models are enabled', () => {
    const groups = getAvailableModels(base({
      openai: { enabled: true, enabled_models: ['gpt-6'] },
      'codex-local': { enabled: true, enabled_models: ['codex:gpt-6-astra'] },
    }));
    expect(groups.find((g) => g.providerKey === 'codex-local')).toEqual({
      providerKey: 'codex-local', providerName: 'Codex (Local CLI)', models: ['codex:gpt-6-astra'],
      labels: { 'codex:gpt-6-astra': 'gpt-6-astra' },
    });
  });
});
