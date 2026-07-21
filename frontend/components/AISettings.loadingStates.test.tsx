/**
 * Regression: task-assignment pickers must not flash a wrong value while the
 * async option sources (platform models / agents / governance) are still
 * loading.
 *
 * The bug: a user's stored value (e.g. transcription = "nous:mediahub-moss-asr")
 * isn't in the option list until getNousModels resolves. A native <select> —
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
import type { AISettings as AISettingsType, NousModelPublic } from '../types';

// Deferred promises we resolve mid-test to simulate the slow async sources.
let resolveNousModels: (m: NousModelPublic[]) => void = () => {};
const nousModelsPromise = new Promise<NousModelPublic[]>((res) => {
  resolveNousModels = res;
});

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn().mockResolvedValue(undefined),
  testAIConnection: vi.fn(),
  reportProviderHealth: vi.fn().mockResolvedValue(undefined),
  // Pending until the test resolves it — simulates a slow platform-models fetch.
  getNousModels: vi.fn(() => nousModelsPromise),
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
vi.mock('../features/canvas-core/smart/NousCenterVerifyPanel', () => ({
  NousCenterVerifyPanel: () => null,
}));

const MOSS_MODEL: NousModelPublic = {
  name: 'mediahub-moss-asr',
  display_name: 'MOSS ASR',
  type: 'asr',
  pricing_type: 'per_hour',
  pricing_value: 10,
};

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
    render(<AISettings settings={settingsWithNousAsr} onSave={vi.fn()} />);

    // While getNousModels is pending, optionsReady is false: the picker must
    // echo the stored raw value, NOT the first static option. (Text appears in
    // both the visible trigger and the aria-hidden native <select>.)
    expect((await screen.findAllByText('nous:mediahub-moss-asr')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Volcengine bigasr')).toBeNull();

    // Platform models arrive → picker swaps to the real option label. (Post-
    // resolve, "Volcengine bigasr" is a legitimate *available* option in the
    // list — the point is it was never shown as the selected value.)
    resolveNousModels([MOSS_MODEL]);

    expect((await screen.findAllByText(/MOSS ASR \(MediaHub/)).length).toBeGreaterThan(0);
  });
});
