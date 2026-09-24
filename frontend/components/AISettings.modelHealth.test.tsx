/**
 * Platform-model health in AI Settings.
 *
 * 2026-08-14 (#1838) surfaced the probe result as a warning and deliberately
 * kept failing models listed. 2026-09-24 reversed that at the user's request:
 * the admin AI Models page shows a failed row as broken, so the user side no
 * longer offers it — on the platform card or in the task pickers. `ok`,
 * `not_probed` and never-probed rows stay listed.
 *
 * The one place a failed model still appears is a task assignment that
 * already points at it: the picker keeps it as an explicit "unavailable"
 * option carrying the reason, instead of silently showing another model.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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

async function renderWith(
  models: NousModelPublic[],
  taskAssignment: Partial<AISettingsType['task_assignment']> = {},
) {
  const { getNousModels } = await import('../services/aiService');
  vi.mocked(getNousModels).mockResolvedValue(models);
  const settings: AISettingsType = {
    ...baseSettings,
    task_assignment: { ...baseSettings.task_assignment, ...taskAssignment },
  };
  const result = render(<AISettings settings={settings} onSave={vi.fn()} />);
  // Wait on a row that stays listed: the first healthy one.
  const anchor = models.find((m) => m.last_test_status !== 'fail');
  if (anchor) {
    await waitFor(() => {
      expect(cardRow(anchor.name)).toBeInTheDocument();
    });
  }
  return result;
}

/** The platform-card row for a model, by row name. */
function cardRow(name: string): HTMLElement {
  const row = document.querySelector(
    `[data-testid="platform-model-row"][data-model-name="${name}"]`,
  );
  if (!row) throw new Error(`row not found for ${name}`);
  return row as HTMLElement;
}

function queryCardRow(name: string): Element | null {
  return document.querySelector(
    `[data-testid="platform-model-row"][data-model-name="${name}"]`,
  );
}

describe('AISettings — platform model health', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('hides a failing model from the platform card', async () => {
    await renderWith([SICK_LLM, WELL_LLM]);
    expect(cardRow(WELL_LLM.name)).toBeInTheDocument();
    expect(queryCardRow(SICK_LLM.name)).toBeNull();
  });

  it('hides a failing image model too — the rule is by status, not by type', async () => {
    // Image rows are only probed by the admin Test button now (the hourly poll
    // writes `not_probed`), so a `fail` on one is a real verdict.
    await renderWith([SICK_IMAGE, WELL_LLM]);
    expect(queryCardRow(SICK_IMAGE.name)).toBeNull();
  });

  it('keeps a never-probed model listed — no signal is not a failure', async () => {
    await renderWith([UNPROBED_ASR, WELL_LLM]);
    expect(cardRow(UNPROBED_ASR.name)).toBeInTheDocument();
  });

  it('does not offer a failing model in the task picker', async () => {
    await renderWith([SICK_LLM, WELL_LLM]);
    expect(screen.getAllByText(/DeepSeek V4 Pro \(Platform\)/).length).toBeGreaterThan(0);
    expect(screen.queryAllByText(/DeepSeek V4 Flash/)).toHaveLength(0);
  });

  it('keeps the healthy model label clean in the task picker', async () => {
    await renderWith([SICK_LLM, WELL_LLM]);
    const healthy = screen.getAllByText(/DeepSeek V4 Pro \(Platform\)/);
    expect(healthy[0].textContent).not.toContain('health check failed');
  });

  it('says a saved-but-failing model is unavailable, with the reason, instead of swapping it', async () => {
    await renderWith([RATE_LIMITED_LLM, WELL_LLM], {
      summarization: `nous:${RATE_LIMITED_LLM.name}`,
    });
    const option = document.querySelector(
      `option[data-unavailable="true"][value="nous:${RATE_LIMITED_LLM.name}"]`,
    );
    expect(option).not.toBeNull();
    expect(option!.textContent).toContain('unavailable');
    expect(option!.textContent).toContain('Rate limited');
    expect(option!.textContent).toContain('20m ago');
    // The select still holds the saved value.
    const select = option!.closest('select') as HTMLSelectElement;
    expect(select.value).toBe(`nous:${RATE_LIMITED_LLM.name}`);
  });

  it('does the same for a saved transcription model', async () => {
    const sickAsr: NousModelPublic = {
      ...UNPROBED_ASR,
      name: 'nous-moss-asr',
      last_test_status: 'fail',
      last_tested_at: twentyMinutesAgo,
    };
    await renderWith([sickAsr, WELL_LLM], { transcription: `nous:${sickAsr.name}` });
    const option = document.querySelector(
      `option[value="nous:${sickAsr.name}"]`,
    ) as HTMLOptionElement | null;
    expect(option).not.toBeNull();
    expect(option!.textContent).toContain('unavailable');
    // Listed first, so the <select> shows the saved value rather than
    // whichever option happened to come first.
    const select = option!.closest('select') as HTMLSelectElement;
    expect(select.options[0]).toBe(option);
    expect(select.value).toBe(`nous:${sickAsr.name}`);
  });
});
