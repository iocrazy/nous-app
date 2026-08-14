/**
 * Platform-model health in AI Settings (spec 2026-08-14 §F2).
 *
 * The backend has probed these models hourly for months, but the result never
 * reached this page — so a user could assign a task to a model whose probe had
 * been failing all day and only find out when the job failed. Two surfaces
 * here: the platform card row (where you manage the models) and the task
 * pickers (where you pick one).
 *
 * A failing model is marked, never hidden and never disabled: the probe has
 * been wrong in production (2026-08-14, a model marked unreachable that worked
 * end-to-end), so it advises and the user decides.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType, NousModelPublic } from '../types';

const twentyMinutesAgo = new Date(Date.now() - 20 * 60_000).toISOString();

const SICK_LLM: NousModelPublic = {
  name: 'mediahub-deepseek-v4-flash',
  display_name: 'DeepSeek V4 Flash',
  type: 'llm',
  pricing_type: 'per_token',
  pricing_value: 2,
  last_test_status: 'fail',
  last_tested_at: twentyMinutesAgo,
};
const WELL_LLM: NousModelPublic = {
  name: 'mediahub-deepseek-v4-pro',
  display_name: 'DeepSeek V4 Pro',
  type: 'llm',
  pricing_type: 'per_token',
  pricing_value: 4,
  last_test_status: 'ok',
  last_tested_at: twentyMinutesAgo,
};
const UNPROBED_ASR: NousModelPublic = {
  name: 'moss-asr',
  display_name: 'MOSS ASR',
  type: 'asr',
  pricing_type: 'per_hour',
  pricing_value: 3,
};

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

async function renderWith(models: NousModelPublic[]) {
  const { getNousModels } = await import('../services/aiService');
  vi.mocked(getNousModels).mockResolvedValue(models);
  const result = render(<AISettings settings={baseSettings} onSave={vi.fn()} />);
  await waitFor(() => {
    expect(screen.getByText(models[0].display_name)).toBeInTheDocument();
  });
  return result;
}

/** The platform-card row for a model, by display name. */
function cardRow(displayName: string): HTMLElement {
  const row = screen.getByText(displayName).closest('.justify-between');
  if (!row) throw new Error(`row not found for ${displayName}`);
  return row as HTMLElement;
}

describe('AISettings — platform model health', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('flags a failing model on its platform-card row, with the check time', async () => {
    await renderWith([SICK_LLM, WELL_LLM]);
    const badge = within(cardRow('DeepSeek V4 Flash')).getByTestId('model-health-badge');
    expect(badge.textContent).toContain('20m ago');
  });

  it('leaves a healthy model unflagged', async () => {
    await renderWith([SICK_LLM, WELL_LLM]);
    expect(
      within(cardRow('DeepSeek V4 Pro')).queryByTestId('model-health-badge'),
    ).toBeNull();
  });

  it('says nothing about a model that has never been probed', async () => {
    await renderWith([UNPROBED_ASR]);
    expect(within(cardRow('MOSS ASR')).queryByTestId('model-health-badge')).toBeNull();
  });

  it('still offers the failing model in the task picker — marked, not removed', async () => {
    await renderWith([SICK_LLM, WELL_LLM]);
    // The option label carries the warning; the model stays selectable because
    // the probe is advisory (it has produced a false negative in production).
    const marked = screen.getAllByText(/DeepSeek V4 Flash \(Platform\).*health check failed/);
    expect(marked.length).toBeGreaterThan(0);
    expect(marked[0].textContent).toContain('20m ago');
  });

  it('keeps the healthy model label clean in the task picker', async () => {
    await renderWith([SICK_LLM, WELL_LLM]);
    const healthy = screen.getAllByText(/DeepSeek V4 Pro \(Platform\)/);
    expect(healthy[0].textContent).not.toContain('health check failed');
  });
});
