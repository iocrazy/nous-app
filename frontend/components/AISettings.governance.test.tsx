/**
 * Tests for Step 3 — AI Config Governance: frontend enforcement.
 *
 * Spec: GET /api/v1/ai/governance → { module: bool } (true = user may configure)
 *
 * Task Assignment hiding rules (Media tab):
 *   - module flag false → row hidden, "Managed by your administrator" shown
 *   - module flag true (or missing) → row visible, unchanged
 *
 * AI Providers section hiding rule:
 *   - ALL six governance modules locked → section hidden
 *   - ANY module allowed → section visible
 *   - Fetch error → fail-open (everything visible)
 *
 * Module names: chat, transcription, translation, visual_analysis, caption,
 *               classification. (summarization is not governed — always shown.)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType } from '../types';

// ── shared mocks ──────────────────────────────────────────────────────────────

const ALL_ALLOWED = {
  chat: true, transcription: true, translation: true,
  visual_analysis: true, caption: true, classification: true,
};

const ALL_LOCKED = {
  chat: false, transcription: false, translation: false,
  visual_analysis: false, caption: false, classification: false,
};

// vi.mock is hoisted — use inline literals, not external variables.
vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn().mockResolvedValue(undefined),
  testAIConnection: vi.fn(),
  getNousModels: vi.fn().mockResolvedValue([]),
  // Default: all allowed. Individual tests override via mockResolvedValueOnce.
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
  useSettingsStore: (selector: (s: { enabledSbProviders: Record<string, boolean> }) => unknown) =>
    selector({ enabledSbProviders: {} }),
}));

// ── helpers ───────────────────────────────────────────────────────────────────

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
  return render(<AISettings settings={{ ...baseSettings, ...overrides }} onSave={vi.fn()} />);
}

/** Re-import getAIGovernance so we can control its return value per test. */
async function setGovernanceMock(flags: Record<string, boolean>) {
  const mod = await import('../services/aiService');
  vi.mocked(mod.getAIGovernance).mockResolvedValueOnce(flags as ReturnType<typeof ALL_ALLOWED>);
}

const MANAGED_TEXT = 'Managed by your administrator';

// ── tests ─────────────────────────────────────────────────────────────────────

describe('AISettings governance — Task Assignment rows', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows all module rows when all governance flags are true (default)', async () => {
    renderSettings();
    await waitFor(() => {
      expect(screen.getByText('Transcription')).toBeInTheDocument();
      expect(screen.getByText('Visual Analysis')).toBeInTheDocument();
      expect(screen.getByText('Translation')).toBeInTheDocument();
      expect(screen.getByText('Caption (Image → Prompt)')).toBeInTheDocument();
      expect(screen.getByText('Classification (Auto Tag)')).toBeInTheDocument();
    });
    // Should NOT show managed note
    expect(screen.queryByText(MANAGED_TEXT)).toBeNull();
  });

  it('hides Transcription row and shows managed note when transcription=false', async () => {
    const { getAIGovernance } = await import('../services/aiService');
    vi.mocked(getAIGovernance).mockResolvedValueOnce({
      ...ALL_ALLOWED,
      transcription: false,
    });

    renderSettings();

    // The label is still rendered (dimmed) but the selector is hidden
    await waitFor(() => {
      expect(screen.getByText(MANAGED_TEXT)).toBeInTheDocument();
    });

    // The transcription <select> element should not be present
    // (we check the absence of the select options by querying a known option text)
    expect(screen.queryByRole('combobox', { name: /transcription/i })).toBeNull();
  });

  it('keeps Transcription row visible when transcription=true', async () => {
    // Default mock returns all-allowed, so no override needed.
    renderSettings();

    // The select for transcription should render (it shows "No Provider Enabled"
    // when no providers are enabled — that's fine, it proves the row is there)
    await waitFor(() => {
      expect(screen.getByText('Transcription')).toBeInTheDocument();
    });
    expect(screen.queryByText(MANAGED_TEXT)).toBeNull();
  });

  it('hides Visual Analysis row when visual_analysis=false', async () => {
    const { getAIGovernance } = await import('../services/aiService');
    vi.mocked(getAIGovernance).mockResolvedValueOnce({
      ...ALL_ALLOWED,
      visual_analysis: false,
    });

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText(MANAGED_TEXT)).toBeInTheDocument();
    });
  });

  it('hides Translation row when translation=false', async () => {
    const { getAIGovernance } = await import('../services/aiService');
    vi.mocked(getAIGovernance).mockResolvedValueOnce({
      ...ALL_ALLOWED,
      translation: false,
    });

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText(MANAGED_TEXT)).toBeInTheDocument();
    });
  });

  it('hides Caption row when caption=false', async () => {
    const { getAIGovernance } = await import('../services/aiService');
    vi.mocked(getAIGovernance).mockResolvedValueOnce({
      ...ALL_ALLOWED,
      caption: false,
    });

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText(MANAGED_TEXT)).toBeInTheDocument();
    });
  });

  it('hides Classification row when classification=false', async () => {
    const { getAIGovernance } = await import('../services/aiService');
    vi.mocked(getAIGovernance).mockResolvedValueOnce({
      ...ALL_ALLOWED,
      classification: false,
    });

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText(MANAGED_TEXT)).toBeInTheDocument();
    });
  });

  it('shows multiple managed notes when multiple modules are locked', async () => {
    const { getAIGovernance } = await import('../services/aiService');
    vi.mocked(getAIGovernance).mockResolvedValueOnce({
      ...ALL_ALLOWED,
      transcription: false,
      visual_analysis: false,
    });

    renderSettings();

    await waitFor(() => {
      // Two managed notes — one for transcription, one for visual_analysis
      expect(screen.getAllByText(MANAGED_TEXT)).toHaveLength(2);
    });
  });

  it('always shows Summarization row regardless of governance (not governed)', async () => {
    const { getAIGovernance } = await import('../services/aiService');
    // Even with all modules locked, summarization should be visible
    vi.mocked(getAIGovernance).mockResolvedValueOnce({ ...ALL_LOCKED });

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText('Summarization')).toBeInTheDocument();
    });
  });
});

describe('AISettings governance — AI Providers section', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows AI Providers section when all modules are allowed (default)', async () => {
    renderSettings();

    await waitFor(() => {
      expect(screen.getByText('AI Providers')).toBeInTheDocument();
    });
  });

  it('shows AI Providers section when only some modules are locked (partial lock)', async () => {
    const { getAIGovernance } = await import('../services/aiService');
    vi.mocked(getAIGovernance).mockResolvedValueOnce({
      chat: false,
      transcription: false,
      translation: false,
      visual_analysis: false,
      caption: true,   // one module still allowed
      classification: false,
    });

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText('AI Providers')).toBeInTheDocument();
    });
  });

  it('hides AI Providers section when ALL six modules are locked', async () => {
    const { getAIGovernance } = await import('../services/aiService');
    vi.mocked(getAIGovernance).mockResolvedValueOnce({ ...ALL_LOCKED });

    renderSettings();

    // Give governance fetch time to resolve and re-render
    await waitFor(() => {
      // When all locked, the managed notes should appear
      expect(screen.getAllByText(MANAGED_TEXT).length).toBeGreaterThanOrEqual(1);
    });

    expect(screen.queryByText('AI Providers')).toBeNull();
  });

  it('shows AI Providers section on governance fetch error (fail-open)', async () => {
    const { getAIGovernance } = await import('../services/aiService');
    vi.mocked(getAIGovernance).mockRejectedValueOnce(new Error('Network error'));

    renderSettings();

    // Even after the failed fetch, governance defaults to all-allowed
    await waitFor(() => {
      expect(screen.getByText('AI Providers')).toBeInTheDocument();
    });
  });

  it('shows everything visible on fetch error (fail-open — no managed notes)', async () => {
    const { getAIGovernance } = await import('../services/aiService');
    vi.mocked(getAIGovernance).mockRejectedValueOnce(new Error('Timeout'));

    renderSettings();

    await waitFor(() => {
      expect(screen.getByText('Transcription')).toBeInTheDocument();
      expect(screen.getByText('AI Providers')).toBeInTheDocument();
    });
    expect(screen.queryByText(MANAGED_TEXT)).toBeNull();
  });
});
