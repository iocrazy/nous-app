/**
 * Platform-model status in AI Settings (spec 2026-09-25).
 *
 * The platform list rides on the settings: the server has already dropped
 * rows whose probe failed and rows nous-engine no longer serves, so the page
 * never sees a `fail` row. What it still decides:
 *   - an `idle` row (authorized on nous-engine, not loaded) stays listed but
 *     cannot be picked;
 *   - `not_probed` is not a verdict — listed and pickable;
 *   - the live status from GET /ai/platform-status (hooks/usePlatformStatus)
 *     wins over the one the settings carried;
 *   - a task assignment pointing at a row the server no longer lists is kept
 *     as an explicit "unavailable" option instead of being swapped — but only
 *     when the list is KNOWN (`platform_models: null` is "could not compute").
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType } from '../types';
import type { PlatformStatusResponse } from '../types/api';
import { _resetPlatformStatusCache } from '../hooks/usePlatformStatus';
import {
  baseAISettings,
  platformStatusWire,
  withPlatform,
  type PlatformRowSpec,
} from '../tests/fixtures/platform';
import en from '../public/locales/en.json';

// Resolve against the REAL shipped English copy rather than a hand-written
// table, so a missing/renamed key surfaces here as a failing assertion instead
// of a raw `aiSettings.*` key shown to users. The component's
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

const WELL_LLM: PlatformRowSpec = {
  name: 'mediahub-deepseek-v4-pro',
  actual_model: '',
  type: 'llm',
  pricing_value: 4,
};
const UNPROBED_ASR: PlatformRowSpec = {
  name: 'moss-asr',
  actual_model: '',
  type: 'asr',
  pricing_type: 'per_hour',
  pricing_value: 3,
  status: 'not_probed',
};
const IDLE_LLM: PlatformRowSpec = {
  name: 'nous-qwen3-8-27b',
  actual_model: 'qwen3-8-27b',
  type: 'llm',
  status: 'idle',
};
const IDLE_ASR: PlatformRowSpec = { ...UNPROBED_ASR, name: 'nous-local-asr', status: 'idle' };

// Pending by default (the settings' status is what shows); a test that wants
// the live answer resolves it.
let resolveStatus: (s: PlatformStatusResponse) => void = () => {};
const getPlatformStatus = vi.fn(
  () => new Promise<PlatformStatusResponse>((res) => {
    resolveStatus = res;
  }),
);

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn(async (s: unknown) => s), // PUT echoes the saved settings
  testAIConnection: vi.fn(),
  getPlatformStatus: () => getPlatformStatus(),
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

async function renderWith(
  rows: PlatformRowSpec[],
  taskAssignment: Partial<AISettingsType['task_assignment']> = {},
  override?: Partial<AISettingsType>,
) {
  const base = baseAISettings();
  const settings: AISettingsType = {
    ...withPlatform(
      { ...base, task_assignment: { ...base.task_assignment, ...taskAssignment } },
      rows,
    ),
    ...override,
  };
  const result = render(<AISettings settings={settings} onSave={vi.fn()} />);
  if (rows.length > 0 && override?.platform_models !== null) {
    await waitFor(() => expect(cardRow(rows[0].name)).toBeInTheDocument());
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

function optionFor(value: string): HTMLOptionElement[] {
  return Array.from(document.querySelectorAll(`option[value="${value}"]`)) as HTMLOptionElement[];
}

beforeEach(() => {
  vi.clearAllMocks();
  _resetPlatformStatusCache();
});

describe('AISettings — platform rows the server no longer lists', () => {
  it('keeps a never-probed model listed and pickable — no signal is not a failure', async () => {
    await renderWith([UNPROBED_ASR, WELL_LLM]);
    expect(cardRow(UNPROBED_ASR.name)).toBeInTheDocument();
    const [asr] = optionFor(`nous:${UNPROBED_ASR.name}`);
    expect(asr.disabled).toBe(false);
  });

  it('says a saved model the server dropped is unavailable, instead of swapping it', async () => {
    await renderWith([WELL_LLM], { summarization: 'nous:mediahub-doubao-seed-2-0-pro' });
    const option = document.querySelector(
      'option[data-unavailable="true"][value="nous:mediahub-doubao-seed-2-0-pro"]',
    );
    expect(option).not.toBeNull();
    expect(option!.textContent).toContain('unavailable');
    // The select still holds the saved value.
    const select = option!.closest('select') as HTMLSelectElement;
    expect(select.value).toBe('nous:mediahub-doubao-seed-2-0-pro');
  });

  it('does the same for a saved transcription model, listed first', async () => {
    await renderWith([WELL_LLM], { transcription: 'nous:nous-moss-asr' });
    const option = document.querySelector(
      'option[value="nous:nous-moss-asr"]',
    ) as HTMLOptionElement | null;
    expect(option).not.toBeNull();
    expect(option!.textContent).toContain('unavailable');
    const select = option!.closest('select') as HTMLSelectElement;
    expect(select.options[0]).toBe(option);
    expect(select.value).toBe('nous:nous-moss-asr');
  });

  it('does not call a saved model unavailable when the list itself is unknown', async () => {
    await renderWith([], { summarization: 'nous:mediahub-deepseek-v4-pro' }, { platform_models: null });
    await waitFor(() => expect(screen.getByText('No platform models available.')).toBeInTheDocument());
    expect(document.querySelector('option[data-unavailable="true"]')).toBeNull();
  });
});

describe('AISettings — platform model not loaded on nous-engine', () => {
  it('shows an idle LLM in the task pickers, disabled, with the reason', async () => {
    await renderWith([IDLE_LLM, WELL_LLM]);
    const idle = optionFor(`nous:${IDLE_LLM.name}`);
    expect(idle.length).toBeGreaterThan(0);
    for (const o of idle) {
      expect(o.disabled).toBe(true);
      expect(o.dataset.description).toBe('Not loaded on nous-engine');
    }
    for (const o of optionFor(`nous:${WELL_LLM.name}`)) expect(o.disabled).toBe(false);
  });

  it('shows an idle ASR model in the transcription picker, disabled', async () => {
    await renderWith([IDLE_ASR, WELL_LLM]);
    const [idle] = optionFor(`nous:${IDLE_ASR.name}`);
    expect(idle.disabled).toBe(true);
    expect(idle.dataset.description).toBe('Not loaded on nous-engine');
  });

  it('keeps a saved idle value selected, dimmed and titled — never swapped', async () => {
    await renderWith([IDLE_LLM, WELL_LLM], { summarization: `nous:${IDLE_LLM.name}` });
    const selected = optionFor(`nous:${IDLE_LLM.name}`)
      .map((o) => o.closest('select') as HTMLSelectElement)
      .find((s) => s.value === `nous:${IDLE_LLM.name}`);
    expect(selected).toBeDefined();
    const trigger = selected!.parentElement!.querySelector('button') as HTMLButtonElement;
    expect(trigger.getAttribute('title')).toBe('Not loaded on nous-engine');
  });

  it('lists an idle row on the platform card as a chip tagged "(not loaded)"', async () => {
    await renderWith([IDLE_LLM, WELL_LLM]);
    const idleChip = cardRow(IDLE_LLM.name);
    expect(idleChip.textContent).toContain('qwen3-8-27b');
    const tag = idleChip.querySelector('[data-testid="non-chat-kind-tag"]');
    expect(tag?.textContent).toBe('LLM (not loaded)');
    expect(tag?.getAttribute('title')).toBe('Not loaded on nous-engine');
    const wellTag = cardRow(WELL_LLM.name).querySelector('[data-testid="non-chat-kind-tag"]');
    expect(wellTag?.textContent).toBe('LLM');
  });

  it('the live status wins: an idle row that nous-engine has since loaded becomes pickable', async () => {
    await renderWith([IDLE_LLM, WELL_LLM]);
    expect(optionFor(`nous:${IDLE_LLM.name}`)[0].disabled).toBe(true);

    resolveStatus(platformStatusWire({ [IDLE_LLM.name]: { status: 'ok' }, [WELL_LLM.name]: {} }));

    await waitFor(() => {
      for (const o of optionFor(`nous:${IDLE_LLM.name}`)) expect(o.disabled).toBe(false);
    });
    const tag = cardRow(IDLE_LLM.name).querySelector('[data-testid="non-chat-kind-tag"]');
    expect(tag?.textContent).toBe('LLM');
    expect(getPlatformStatus).toHaveBeenCalledTimes(1);
  });
});
