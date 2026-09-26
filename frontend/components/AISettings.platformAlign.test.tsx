/**
 * The user-side platform list must match Admin → AI Models (2026-09-24):
 *
 *   1. Same NAME. Admin labels a row by `actual_model`
 *      (`doubao-embedding-vision-251215`), falling back to the row `name` when
 *      `actual_model` is empty. The user side used to print `display_name`
 *      (`Doubao Embedding (Vision)`), so the two pages could not be matched;
 *      since 2026-09-25 `display_name` is not shown on the user side at all.
 *   2. Same AVAILABILITY. `not_probed` rows (image / local-daemon rows the
 *      hourly poll cannot judge) stay listed. Failed rows never reach the
 *      page: the server drops them from the settings view (spec 2026-09-25,
 *      pinned by backend/tests/api/test_ai_settings_wire.py).
 *
 * Fixture = the 14 rows the 2026-09-24 recon found (8 `ok`, 6 `not_probed`;
 * jimeng-local rows carry an EMPTY `actual_model`), in the `GET /ai/settings`
 * platform shape (tests/fixtures/platform). The old `display_name`s are kept
 * beside each row only to prove none of them reaches the page.
 * Reconstructed from the recon and migration seeds, not a verbatim dump.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { PlatformModelType } from '../types/api';
import { baseAISettings, withPlatform, type PlatformRowSpec } from '../tests/fixtures/platform';

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

type Row = PlatformRowSpec & { display_name: string };

function row(
  name: string,
  display_name: string,
  type: PlatformModelType,
  actual_model: string,
  status: 'ok' | 'not_probed',
  extra: Partial<PlatformRowSpec> = {},
): Row {
  return {
    name,
    display_name,
    actual_model,
    type,
    pricing_type: type === 'asr' ? 'per_hour' : type === 'llm' ? 'per_token' : 'per_request',
    pricing_value: 0,
    status,
    is_local: false,
    context_window_tokens: null,
    ...extra,
  };
}

const PRODUCTION_ROWS: Row[] = [
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

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn(async (s: unknown) => s), // PUT echoes the saved settings
  testAIConnection: vi.fn(),
  getPlatformStatus: vi.fn(() => new Promise(() => {})),
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

const rowEl = (name: string) =>
  document.querySelector(`[data-testid="platform-model-row"][data-model-name="${name}"]`);

async function renderRows(rows: Row[]) {
  // `display_name` is not a wire key any more; strip it before building the body.
  const wire = rows.map(({ display_name: _dn, ...r }) => r);
  render(<AISettings settings={withPlatform(baseAISettings(), wire)} onSave={vi.fn()} />);
  await waitFor(() => expect(rowEl('nous-qwen3-llm')).not.toBeNull());
}

describe('AISettings — platform list matches Admin → AI Models', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('names each row exactly the way admin does: actual_model, and no display_name', async () => {
    await renderRows(PRODUCTION_ROWS);
    const el = rowEl('nous-doubao-embedding-vision')!;
    const spans = el.querySelectorAll('span');
    expect(spans[0].textContent).toBe('doubao-embedding-vision-251215');
    // The only other text on the chip is its type tag.
    expect(spans[1].textContent).toBe('EMBEDDING');
    expect(el.textContent).toBe('doubao-embedding-vision-251215EMBEDDING');
    // No production display_name reaches the page anywhere.
    for (const r of PRODUCTION_ROWS) {
      expect(document.body.textContent, r.name).not.toContain(r.display_name);
    }
  });

  it('falls back to the row name for jimeng-local rows with an empty actual_model', async () => {
    await renderRows(PRODUCTION_ROWS);
    const el = rowEl('jimeng-local-image')!;
    expect(el.querySelector('span')!.textContent).toBe('jimeng-local-image');
  });

  it('lists all 14 production rows, including the not_probed ones', async () => {
    await renderRows(PRODUCTION_ROWS);
    for (const r of PRODUCTION_ROWS) {
      expect(rowEl(r.name), r.name).not.toBeNull();
    }
  });

  it('uses the admin label in the task pickers but keeps nous:<name> as the value', async () => {
    await renderRows(PRODUCTION_ROWS);
    const llm = document.querySelector(
      'option[value="nous:nous-deepseek-v4-pro"]',
    ) as HTMLOptionElement;
    expect(llm.textContent).toBe('deepseek-v4-pro (Platform)');
    const asr = document.querySelector('option[value="nous:nous-moss-asr"]') as HTMLOptionElement;
    expect(asr.textContent).toMatch(/^moss-asr \(Platform · /);
    expect(asr.textContent).not.toContain('MOSS ASR');
  });
});
