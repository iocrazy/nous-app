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
import en from '../public/locales/en.json';

// Resolve against the REAL shipped English copy rather than a hand-written
// table, so a missing/renamed key surfaces here as a failing assertion instead
// of a raw `aiSettings.modelHealthFailedAgo` shown to users. The component's
// own i18n instance is never initialized in tests (nothing loads i18n.ts), so
// react-i18next would otherwise hand back bare keys.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, unknown>): string => {
      const template = key
        .split('.')
        .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
      if (typeof template !== 'string') return key;
      return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars?.[name] ?? ''));
    },
  }),
}));

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
/** Same red light as SICK_LLM, but the backend classified WHY (mig 427). */
const RATE_LIMITED_LLM: NousModelPublic = {
  ...SICK_LLM,
  name: 'mediahub-doubao-seed-2-0-pro',
  display_name: 'Doubao Seed 2.0 Pro',
  last_test_code: 'rate_limit',
};
/** Recorded as failing by construction — see the no-badge test below. */
const SICK_IMAGE: NousModelPublic = {
  name: 'mediahub-doubao-seedream-t2i',
  display_name: 'Seedream T2I',
  type: 'image',
  pricing_type: 'per_request',
  pricing_value: 5,
  last_test_status: 'fail',
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

  it('says nothing about an image model, whose red light is a probe artifact', async () => {
    // The backend probe POSTs every non-asr/embedding type to
    // /chat/completions, so image / video / tts models are recorded as failing
    // no matter what. On 2026-08-14, 3 of the 4 production `fail` rows were
    // exactly this — and all three models worked. Three permanently-lit
    // warnings would teach users to ignore the badge, which is the failure
    // this feature exists to prevent.
    await renderWith([SICK_IMAGE, SICK_LLM]);
    expect(within(cardRow('Seedream T2I')).queryByTestId('model-health-badge')).toBeNull();
    // Same render, real signal still shown — proves the filter is by type and
    // not a blanket mute.
    expect(
      within(cardRow('DeepSeek V4 Flash')).getByTestId('model-health-badge'),
    ).toBeInTheDocument();
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

  it('says WHY the check failed, when the backend classified it', async () => {
    // 2026-08-14 produced both of these in one round, and they ask for opposite
    // things: the timeout was a local engine still loading (wait), the 429 was
    // a quota (go act). Under #1838 both rendered as "health check failed".
    // The wording resolves through the shipped en.json, so a missing
    // healthReason key fails here instead of showing users a raw key.
    await renderWith([RATE_LIMITED_LLM, WELL_LLM]);
    const badge = within(cardRow('Doubao Seed 2.0 Pro')).getByTestId('model-health-badge');
    expect(badge.textContent).toContain('Rate limited');
    expect(badge.textContent).toContain('20m ago');
  });

  it('falls back to the plain wording when there is no code to name', async () => {
    // Rows probed before the column existed. The badge must still appear —
    // losing the warning to gain a reason would be a straight regression.
    await renderWith([SICK_LLM, WELL_LLM]);
    const badge = within(cardRow('DeepSeek V4 Flash')).getByTestId('model-health-badge');
    expect(badge.textContent).toContain('health check failed');
    expect(badge.textContent).not.toContain(':');
  });

  it('still offers a rate-limited model in the picker — reason shown, not vetoed', async () => {
    // #1838's rule survives the added reason: knowing why it failed is not a
    // reason to start blocking the choice.
    await renderWith([RATE_LIMITED_LLM, WELL_LLM]);
    const marked = screen.getAllByText(
      /Doubao Seed 2\.0 Pro \(Platform\).*health check failed: Rate limited/,
    );
    expect(marked.length).toBeGreaterThan(0);
  });

  it('keeps the healthy model label clean in the task picker', async () => {
    await renderWith([SICK_LLM, WELL_LLM]);
    const healthy = screen.getAllByText(/DeepSeek V4 Pro \(Platform\)/);
    expect(healthy[0].textContent).not.toContain('health check failed');
  });
});
