/**
 * Tests for the "MediaHub (Platform)" models card.
 *
 * Two fixes under test:
 *   1. The card renders ALL enabled platform model types (llm / embedding /
 *      asr / image), not just llm.
 *   2. The right side shows a neutral TYPE badge — never the admin-internal
 *      `description` note (which used to leak private IPs / BYOK refs).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType, NousModelPublic } from '../types';

const FOUR_TYPES: NousModelPublic[] = [
  {
    name: 'doubao-embedding-vision-251215',
    display_name: 'Doubao Embedding Vision',
    type: 'embedding',
    pricing_type: 'per_request',
    pricing_value: 1,
  },
  {
    name: 'moss-asr',
    display_name: 'MOSS ASR',
    type: 'asr',
    pricing_type: 'per_hour',
    pricing_value: 3,
  },
  {
    name: 'nous-llm',
    display_name: 'Nous LLM',
    type: 'llm',
    pricing_type: 'per_token',
    pricing_value: 2,
  },
  {
    name: 'doubao-seedream-3-0-t2i',
    display_name: 'Doubao Seedream 3.0',
    type: 'image',
    pricing_type: 'per_request',
    pricing_value: 5,
  },
];

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn().mockResolvedValue(undefined),
  testAIConnection: vi.fn(),
  // Overridden per-test.
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

function renderSettings() {
  return render(<AISettings settings={baseSettings} onSave={vi.fn()} />);
}

describe('AISettings — MediaHub (Platform) models card', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders ALL enabled model types (not just llm), each with a type badge', async () => {
    const { getNousModels } = await import('../services/aiService');
    vi.mocked(getNousModels).mockResolvedValue(FOUR_TYPES);

    renderSettings();

    // Every configured model — regardless of type — is listed.
    await waitFor(() => {
      expect(screen.getByText('Nous LLM')).toBeInTheDocument();
    });
    expect(screen.getByText('Doubao Embedding Vision')).toBeInTheDocument();
    expect(screen.getByText('MOSS ASR')).toBeInTheDocument();
    expect(screen.getByText('Doubao Seedream 3.0')).toBeInTheDocument();

    // Each row carries a neutral type badge.
    expect(screen.getByText('llm')).toBeInTheDocument();
    expect(screen.getByText('embedding')).toBeInTheDocument();
    expect(screen.getByText('asr')).toBeInTheDocument();
    expect(screen.getByText('image')).toBeInTheDocument();
  });

  it('sorts rows llm → asr → embedding → image', async () => {
    const { getNousModels } = await import('../services/aiService');
    vi.mocked(getNousModels).mockResolvedValue(FOUR_TYPES);

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText('Nous LLM')).toBeInTheDocument();
    });

    const displayed = screen
      .getAllByText(/^(Nous LLM|MOSS ASR|Doubao Embedding Vision|Doubao Seedream 3\.0)$/)
      .map((el) => el.textContent);
    expect(displayed).toEqual([
      'Nous LLM',
      'MOSS ASR',
      'Doubao Embedding Vision',
      'Doubao Seedream 3.0',
    ]);
  });

  it('shows the empty-state copy when no platform models are configured', async () => {
    const { getNousModels } = await import('../services/aiService');
    vi.mocked(getNousModels).mockResolvedValue([]);

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText('No platform models available.')).toBeInTheDocument();
    });
  });

  it('never renders an admin description note (even if one sneaks into the payload)', async () => {
    const { getNousModels } = await import('../services/aiService');
    // Simulate a stale/dirty payload carrying an internal ops note.
    const leaky = [
      {
        ...FOUR_TYPES[2],
        // @ts-expect-error — description is intentionally not part of the public type.
        description: 'via ZeroTier (10.0.0.10:8000) from 8512939 BYOK',
      },
    ] as NousModelPublic[];
    vi.mocked(getNousModels).mockResolvedValue(leaky);

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText('Nous LLM')).toBeInTheDocument();
    });
    expect(screen.queryByText(/ZeroTier/)).toBeNull();
    expect(screen.queryByText(/BYOK/)).toBeNull();
    // The row shows the type badge instead.
    const row = screen.getByText('Nous LLM').closest('div')!;
    expect(within(row).getByText('llm')).toBeInTheDocument();
  });
});
