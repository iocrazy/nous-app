/**
 * Regression: persisted BYOK connection-test results must be restored on load.
 *
 * The Test Connection outcome used to live only in React state — gone on
 * reload. It is now persisted into settings_json.ai_provider_health and
 * threaded back through settings.provider_health; the form seeds its
 * connection status / error / "Last tested" line from it so a reload shows
 * the last result without re-testing.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType } from '../types';

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn().mockResolvedValue(undefined),
  testAIConnection: vi.fn(),
  reportProviderHealth: vi.fn().mockResolvedValue(undefined),
  getNousModels: vi.fn().mockResolvedValue([]),
  getAIGovernance: vi.fn().mockResolvedValue({
    chat: true, transcription: true, translation: true,
    visual_analysis: true, caption: true, classification: true,
    summarization: true,
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

const baseSettings: AISettingsType = {
  ai_enabled: true,
  auto_transcribe: false,
  auto_summarize: false,
  preferred_language: 'auto',
  providers: {
    qwen: { enabled: true, api_key: 'sk-qwen', base_url: 'https://x/v1' },
  },
  task_assignment: {
    transcription: '',
    summarization: '',
    visual_analysis: '',
    image_generation: '',
    script_generation: '',
  },
};

describe('AISettings provider_health seeding', () => {
  it('restores a failed test result (detail + Last tested)', async () => {
    const settings: AISettingsType = {
      ...baseSettings,
      provider_health: {
        qwen: {
          status: 'fail',
          detail: 'Error code: 401 - invalid api key',
          tested_at: new Date().toISOString(),
        },
      },
    };

    render(<AISettings settings={settings} onSave={vi.fn()} />);

    // Failed status → button shows "Retry Connection" + the persisted detail.
    expect(await screen.findByText('Retry Connection')).toBeTruthy();
    expect(screen.getByText('Error code: 401 - invalid api key')).toBeTruthy();
    expect(screen.getByText(/Last tested/)).toBeTruthy();
  });

  it('restores a successful test result (Connected + Last tested)', async () => {
    const settings: AISettingsType = {
      ...baseSettings,
      provider_health: {
        qwen: {
          status: 'ok',
          detail: '5 models available',
          tested_at: new Date().toISOString(),
        },
      },
    };

    render(<AISettings settings={settings} onSave={vi.fn()} />);

    expect(await screen.findByText('Connected')).toBeTruthy();
    expect(screen.getByText(/Last tested/)).toBeTruthy();
  });

  it('shows no Last tested line when provider_health is absent', async () => {
    render(<AISettings settings={baseSettings} onSave={vi.fn()} />);

    // Wait for async effects to settle by finding the stable Test button.
    expect(await screen.findByText('Test Connection')).toBeTruthy();
    expect(screen.queryByText(/Last tested/)).toBeNull();
  });
});
