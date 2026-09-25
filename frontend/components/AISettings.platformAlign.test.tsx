/**
 * The user-side platform list must match Admin → AI Models (2026-09-24):
 *
 *   1. Same NAME. Admin labels a row by `actual_model`
 *      (`doubao-embedding-vision-251215`), falling back to the row `name` when
 *      `actual_model` is empty. The user side used to print `display_name`
 *      (`Doubao Embedding (Vision)`), so the two pages could not be matched.
 *   2. Same AVAILABILITY. A row whose last probe failed is not offered;
 *      `not_probed` rows (image / local-daemon rows the hourly poll cannot
 *      judge) stay listed.
 *
 * Fixture = production-shaped `GET /api/v1/ai/nous-models` rows: the 14 rows
 * the 2026-09-24 recon found (8 `ok`, 6 `not_probed`; jimeng-local rows carry
 * an EMPTY `actual_model`), wire types kept as sent (bigint `id` and Numeric
 * `pricing_value` are JSON numbers). Production had no `fail` row that day, so
 * two synthetic failing rows are appended — they are marked as such below.
 * Reconstructed from the recon and migration seeds, not a verbatim dump.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType } from '../types';
import type { NousModelPublic } from '../types/api';

import en from '../public/locales/en.json';

vi.mock('react-i18next', () => {
  const t = (key: string, vars?: Record<string, unknown>): string => {
    const template = key
      .split('.')
      .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
    if (typeof template !== 'string') return key;
    return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars?.[name] ?? ''));
  };
  return { useTranslation: () => ({ t }) };
});

const TESTED = '2026-09-24T01:00:00+00:00';

// The generated row already carries `id` (a JSON number) and `sort_order`.
type WireRow = NousModelPublic;

let nextId = 7_300_000_000_001;
function row(
  name: string,
  display_name: string,
  type: NousModelPublic['type'],
  actual_model: string,
  status: 'ok' | 'not_probed' | 'fail',
  extra: Partial<WireRow> = {},
): WireRow {
  return {
    id: nextId++,
    name,
    display_name,
    actual_model,
    type,
    pricing_type: type === 'asr' ? 'per_hour' : type === 'llm' ? 'per_token' : 'per_request',
    pricing_value: 0,
    sort_order: 10,
    last_test_status: status,
    last_tested_at: TESTED,
    last_test_code: status === 'fail' ? 'timeout' : null,
    is_local: false,
    ...extra,
  };
}

const PRODUCTION_ROWS: WireRow[] = [
  // 8 × ok
  row('nous-qwen3-llm', 'Qwen3 LLM', 'llm', 'qwen3-32b', 'ok'),
  row('nous-deepseek-v4-pro', 'DeepSeek V4 Pro', 'llm', 'deepseek-v4-pro', 'ok'),
  row('nous-deepseek-v4-flash', 'DeepSeek V4 Flash', 'llm', 'deepseek-v4-flash', 'ok'),
  row('nous-doubao-seed-2-0-pro', 'Doubao Seed 2.0 Pro', 'llm', 'doubao-seed-2-0-pro-260215', 'ok'),
  row(
    'nous-doubao-embedding-vision',
    'Doubao Embedding (Vision)',
    'embedding',
    'doubao-embedding-vision-251215',
    'ok',
  ),
  row('nous-qwen3-embedding-8b', 'Qwen3 Embedding 8B', 'embedding', 'qwen3-embedding-8b', 'ok'),
  row('nous-wemm-embedding-4b', 'WeMM Embedding 4B', 'embedding', 'wemm-embedding-4b', 'ok'),
  row('nous-moss-asr', 'MOSS ASR', 'asr', 'moss-asr', 'ok'),
  // 6 × not_probed
  row('nous-studio-upscale', 'Nous Studio Upscale (SeedVR2)', 'image', 'studio-upscale', 'not_probed'),
  row('codex-local-image', 'GPT Image (Codex Local)', 'image', 'gpt-image-2', 'not_probed', {
    is_local: true,
  }),
  row('jimeng-local-image', 'Dreamina Image (Local)', 'image', '', 'not_probed', { is_local: true }),
  row('jimeng-local-video', 'Dreamina Video (Local)', 'video', '', 'not_probed', { is_local: true }),
  row('jimeng-local-image-4k', 'Dreamina Image 4K (Local)', 'image', '', 'not_probed', {
    is_local: true,
  }),
  row('jimeng-local-seedance', 'Dreamina Seedance (Local)', 'video', '', 'not_probed', {
    is_local: true,
  }),
];

// SYNTHETIC — no production row was failing on 2026-09-24.
const FAILING_ROWS: WireRow[] = [
  row('nous-broken-llm', 'Broken LLM', 'llm', 'broken-llm-0101', 'fail'),
  row('nous-broken-embedding', 'Broken Embedding', 'embedding', 'broken-embedding-0101', 'fail'),
];

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

const rowEl = (name: string) =>
  document.querySelector(`[data-testid="platform-model-row"][data-model-name="${name}"]`);

async function renderRows(rows: WireRow[]) {
  const { getNousModels } = await import('../services/aiService');
  vi.mocked(getNousModels).mockResolvedValue(rows);
  render(<AISettings settings={baseSettings} onSave={vi.fn()} />);
  await waitFor(() => expect(rowEl('nous-qwen3-llm')).not.toBeNull());
}

describe('AISettings — platform list matches Admin → AI Models', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('names each row the way admin does: actual_model first, display_name secondary', async () => {
    await renderRows([...PRODUCTION_ROWS, ...FAILING_ROWS]);
    const el = rowEl('nous-doubao-embedding-vision')!;
    const spans = el.querySelectorAll('span');
    expect(spans[0].textContent).toBe('doubao-embedding-vision-251215');
    expect(spans[1].textContent).toBe('Doubao Embedding (Vision)');
  });

  it('falls back to the row name for jimeng-local rows with an empty actual_model', async () => {
    await renderRows(PRODUCTION_ROWS);
    const el = rowEl('jimeng-local-image')!;
    expect(el.querySelector('span')!.textContent).toBe('jimeng-local-image');
  });

  it('lists all 14 production rows, including the not_probed ones', async () => {
    await renderRows([...PRODUCTION_ROWS, ...FAILING_ROWS]);
    for (const r of PRODUCTION_ROWS) {
      expect(rowEl(r.name), r.name).not.toBeNull();
    }
  });

  it('hides failing rows from the card and from every picker', async () => {
    await renderRows([...PRODUCTION_ROWS, ...FAILING_ROWS]);
    for (const r of FAILING_ROWS) {
      expect(rowEl(r.name), r.name).toBeNull();
      expect(document.querySelector(`option[value="nous:${r.name}"]`), r.name).toBeNull();
    }
    expect(document.body.textContent).not.toContain('broken-llm-0101');
    expect(document.body.textContent).not.toContain('broken-embedding-0101');
  });

  it('uses the admin label in the task pickers but keeps nous:<name> as the value', async () => {
    await renderRows(PRODUCTION_ROWS);
    const llm = document.querySelector(
      'option[value="nous:nous-deepseek-v4-pro"]',
    ) as HTMLOptionElement;
    expect(llm.textContent).toBe('deepseek-v4-pro · DeepSeek V4 Pro (Platform)');
    const asr = document.querySelector('option[value="nous:nous-moss-asr"]') as HTMLOptionElement;
    expect(asr.textContent).toMatch(/^moss-asr · MOSS ASR \(Platform · /);
  });
});
