/**
 * Regression: the AI settings form must re-sync when the settings prop
 * hydrates after mount.
 *
 * AuthContext loads AI settings asynchronously after login; the form used
 * to copy the prop into local state once (useState initializer only), so
 * mounting before the fetch resolved left the form on empty defaults even
 * though the DB had data — and saving from that stale state wiped keys.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType } from '../types';

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn().mockResolvedValue(undefined),
  testAIConnection: vi.fn(),
  getNousModels: vi.fn().mockResolvedValue([]),
  getAIGovernance: vi.fn().mockResolvedValue({
    chat: true, transcription: true, translation: true,
    visual_analysis: true, caption: true, classification: true,
  }),
  GOVERNANCE_ALL_ALLOWED: {
    chat: true, transcription: true, translation: true,
    visual_analysis: true, caption: true, classification: true,
  },
}));
vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: { listAgents: vi.fn().mockResolvedValue([]) },
}));
vi.mock('./StoryboardApiSettings', () => ({ StoryboardApiSettings: () => null }));
vi.mock('./MCPServersPanel', () => ({ MCPServersPanel: () => null }));
vi.mock('./ApprovalsPanel', () => ({ ApprovalsPanel: () => null }));
vi.mock('./TokenBillingDashboard', () => ({ TokenBillingDashboard: () => null }));
vi.mock('./MemoryPanel', () => ({ MemoryPanel: () => null }));
vi.mock('./AIHealthBoard', () => ({ AIHealthBoard: () => null }));
vi.mock('../features/canvas-core/smart/NousCenterVerifyPanel', () => ({
  NousCenterVerifyPanel: () => null,
}));
vi.mock('../stores/settingsStore', () => ({
  useSettingsStore: (selector: (s: { enabledSbProviders: string[] }) => unknown) =>
    selector({ enabledSbProviders: [] }),
}));

const QWEN_KEY_PLACEHOLDER = 'Enter your Qwen (Bailian) API key';

const emptySettings: AISettingsType = {
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
};

const loadedSettings: AISettingsType = {
  ...emptySettings,
  providers: {
    qwen: {
      enabled: true,
      api_key: 'sk-loaded-from-server',
      base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    },
  },
};

describe('AISettings prop hydration', () => {
  it('re-syncs the form when settings load after mount', async () => {
    const { rerender } = render(
      <AISettings settings={emptySettings} onSave={vi.fn()} />
    );
    expect(screen.queryByPlaceholderText(QWEN_KEY_PLACEHOLDER)).toBeNull();

    rerender(<AISettings settings={loadedSettings} onSave={vi.fn()} />);

    const input = await screen.findByPlaceholderText<HTMLInputElement>(
      QWEN_KEY_PLACEHOLDER
    );
    expect(input.value).toBe('sk-loaded-from-server');
  });

  it('keeps user edits when the prop refreshes afterwards', async () => {
    const { rerender } = render(
      <AISettings settings={loadedSettings} onSave={vi.fn()} />
    );
    const input = await screen.findByPlaceholderText<HTMLInputElement>(
      QWEN_KEY_PLACEHOLDER
    );
    fireEvent.change(input, { target: { value: 'sk-user-typed' } });

    rerender(<AISettings settings={loadedSettings} onSave={vi.fn()} />);

    expect(
      screen.getByPlaceholderText<HTMLInputElement>(QWEN_KEY_PLACEHOLDER).value
    ).toBe('sk-user-typed');
  });
});
