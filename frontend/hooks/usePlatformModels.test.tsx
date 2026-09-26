// hooks/usePlatformModels.test.tsx
//
// One settings object, one list (spec 2026-09-25 §3.7). Before this spec the
// five platform-model surfaces each fetched their own catalog with their own
// filters; now every one of them maps the same AI settings and overlays the
// same status request. Pinned here:
//   1. the Providers page task pickers, the agent editor, the canvas text
//      pickers and the generation pickers (canvas / cover studio / asset
//      sheet) offer the SAME rows for the same settings;
//   2. all of them together make exactly ONE status request and ZERO catalog
//      requests;
//   3. the generation list applies the daemon rule the server used to apply
//      (`apply_readiness`): local rows the daemon cannot run are hidden, a
//      superseded server twin is hidden, unknown is not a verdict;
//   4. upscale-only rows never reach a generation picker (P2: via the
//      capability table's rows; P3 swaps that for `generatable` — this test
//      stays the gate).

import { render, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AISettings as AISettingsType } from '../types';
import type { PlatformStatusResponse } from '../types/api';
import {
  baseAISettings,
  platformStatusWire,
  withPlatform,
  type PlatformRowSpec,
} from '../tests/fixtures/platform';

const getPlatformStatus = vi.fn<() => Promise<PlatformStatusResponse>>();
const fetchSpy = vi.fn();
vi.mock('../services/aiService', () => ({
  getPlatformStatus: () => getPlatformStatus(),
  saveAISettings: vi.fn(async (s: unknown) => s),
  testAIConnection: vi.fn(),
  reportProviderHealth: vi.fn(),
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
vi.mock('../components/ApprovalsPanel', () => ({ ApprovalsPanel: () => null }));
vi.mock('../components/AIHealthBoard', () => ({ AIHealthBoard: () => null }));
// generation-capabilities is keyed by the server's generation-row predicate,
// which drops upscale-only services — `nous-studio-upscale` is absent here.
const listGenerationCapabilities = vi.fn();
vi.mock('../features/canvas-core/services/canvasGenerationService', () => ({
  listGenerationCapabilities: () => listGenerationCapabilities(),
}));
const auth = vi.hoisted(() => ({ settings: null as unknown }));
vi.mock('../contexts/AuthContext', () => ({
  useOptionalAISettings: () => auth.settings,
}));

import { AISettings } from '../components/AISettings';
import { getAvailableModels } from '../components/AILibrary/agentEditorModel';
import { useGenerationModels } from '../features/canvas-core/smart/nodes/useGenerationModels';
import { _resetModelCapabilitiesCache } from '../features/canvas-core/smart/nodes/useModelCapabilities';
import { _resetPlatformStatusCache } from './usePlatformStatus';
import { useTextPlatformModels } from './usePlatformModels';

// Production-shaped mix: two llm rows (one idle, one switched off), an
// embedding and an asr row no chat or image picker may offer, a server image
// row, and the jimeng / codex local twins.
const ROWS: PlatformRowSpec[] = [
  { name: 'nous-qwen3-8b', actual_model: 'qwen3-8b', type: 'llm' },
  { name: 'nous-qwen3-27b', actual_model: 'qwen3-27b', type: 'llm', status: 'idle' },
  { name: 'nous-deepseek', actual_model: 'deepseek-v4-pro', type: 'llm', disabled: true },
  { name: 'nous-wemm-2b', actual_model: 'wemm-2b', type: 'embedding' },
  { name: 'nous-moss-asr', actual_model: 'moss-asr', type: 'asr', pricing_type: 'per_hour' },
  { name: 'nous-seedream', actual_model: 'doubao-seedream-4-0', type: 'image' },
  // Upscale-only (needs an input image): never a text-to-image choice.
  { name: 'nous-studio-upscale', actual_model: 'studio-upscale', type: 'image', status: 'not_probed' },
  { name: 'jimeng-image', actual_model: 'jimeng-4.0', type: 'image' },
  { name: 'jimeng-local-image', actual_model: '', type: 'image', is_local: true, status: 'not_probed' },
  { name: 'codex-local-image', actual_model: 'gpt-image-2', type: 'image', is_local: true, status: 'not_probed' },
  { name: 'jimeng-local-seedance', actual_model: '', type: 'video', is_local: true, status: 'not_probed' },
];
const SETTINGS: AISettingsType = withPlatform(baseAISettings(), ROWS);
const GENERATABLE = [
  'nous-seedream',
  'jimeng-image',
  'jimeng-local-image',
  'codex-local-image',
  'jimeng-local-seedance',
];
const CAPS = Object.fromEntries(
  GENERATABLE.map((n) => [
    n,
    { ratios: [], quality: false, quality_tiers: [], resolution: false, max_refs: 0, negative: false, video_modes: [] },
  ]),
);

beforeEach(() => {
  _resetPlatformStatusCache();
  _resetModelCapabilitiesCache();
  listGenerationCapabilities.mockReset();
  listGenerationCapabilities.mockResolvedValue(CAPS);
  getPlatformStatus.mockReset();
  auth.settings = SETTINGS;
  fetchSpy.mockReset();
  vi.stubGlobal('fetch', fetchSpy);
});
afterEach(() => {
  vi.unstubAllGlobals();
  _resetPlatformStatusCache();
  _resetModelCapabilitiesCache();
});

describe('useGenerationModels — the daemon rule on the settings list', () => {
  it('before the status lands: every enabled generatable row, unknown is not a verdict', async () => {
    getPlatformStatus.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useGenerationModels());
    await waitFor(() => expect(result.current.map((m) => m.name)).toEqual(GENERATABLE));
  });

  it('upscale-only rows never reach a generation picker', async () => {
    getPlatformStatus.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useGenerationModels('image'));
    await waitFor(() => expect(listGenerationCapabilities).toHaveBeenCalled());
    await waitFor(() =>
      expect(result.current.map((m) => m.name)).not.toContain('nous-studio-upscale'),
    );
    expect(result.current.map((m) => m.name)).toContain('nous-seedream');
  });

  it('an unknown capability table (failed) does not empty the picker', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    listGenerationCapabilities.mockRejectedValue(new Error('boom'));
    getPlatformStatus.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useGenerationModels('image'));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(result.current.map((m) => m.name)).toContain('nous-seedream');
    spy.mockRestore();
  });

  it('hides local rows the daemon cannot run and the server twin a ready local twin supersedes', async () => {
    getPlatformStatus.mockResolvedValue(
      platformStatusWire({
        'nous-seedream': {},
        // Dreamina logged in on the user's machine: local twin offered,
        // server twin superseded.
        'jimeng-image': { superseded: true },
        'jimeng-local-image': { status: 'not_probed', local_ready: true },
        'jimeng-local-seedance': { status: 'not_probed', local_ready: true },
        // Codex card off / daemon not logged in.
        'codex-local-image': { status: 'not_probed', local_ready: false },
      }),
    );
    const { result } = renderHook(() => useGenerationModels('image'));
    await waitFor(() =>
      expect(result.current.map((m) => m.name)).toEqual(['nous-seedream', 'jimeng-local-image']),
    );
  });

  it('filters by kind', async () => {
    getPlatformStatus.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useGenerationModels('video'));
    await waitFor(() => expect(listGenerationCapabilities).toHaveBeenCalled());
    expect(result.current.map((m) => m.name)).toEqual(['jimeng-local-seedance']);
  });

  it('overlays the live status on each row', async () => {
    getPlatformStatus.mockResolvedValue(
      platformStatusWire({ 'nous-seedream': { status: 'idle' } }),
    );
    const { result } = renderHook(() => useGenerationModels('image'));
    await waitFor(() =>
      expect(result.current.find((m) => m.name === 'nous-seedream')?.status).toBe('idle'),
    );
  });

  it('offers nothing outside a loaded session', () => {
    getPlatformStatus.mockReturnValue(new Promise(() => {}));
    auth.settings = null;
    const { result } = renderHook(() => useGenerationModels());
    expect(result.current).toEqual([]);
  });
});

describe('every surface shows the same list from the same settings', () => {
  it('Providers task pickers, agent editor and canvas text pickers offer the same llm rows', async () => {
    getPlatformStatus.mockReturnValue(new Promise(() => {}));
    const expected = ['nous-qwen3-8b', 'nous-qwen3-27b'];

    // Agent editor.
    const agent = getAvailableModels(SETTINGS).find((g) => g.providerKey === 'nous');
    expect(agent?.models).toEqual(expected);

    // Canvas text pickers (LlmNodeView, PromptNodeView).
    const text = renderHook(() => useTextPlatformModels());
    expect(text.result.current.map((m) => m.name)).toEqual(expected);

    // Providers page task pickers: every agent picker offers `nous:<name>`.
    render(<AISettings settings={SETTINGS} onSave={vi.fn()} />);
    await waitFor(() =>
      expect(document.querySelector('option[value="nous:nous-qwen3-8b"]')).not.toBeNull(),
    );
    const firstPicker = document
      .querySelector('option[value="nous:nous-qwen3-8b"]')!
      .closest('select')!;
    const offered = Array.from(firstPicker.options)
      .map((o) => o.value)
      .filter((v) => v.startsWith('nous:'))
      .map((v) => v.slice('nous:'.length));
    expect(offered).toEqual(expected);

    // …and the Providers card lists the same enabled rows as chips (plus the
    // switched-off row, offered by Add Model instead).
    const chips = Array.from(document.querySelectorAll('[data-testid="platform-model-row"]')).map(
      (el) => el.getAttribute('data-model-name'),
    );
    expect(chips).toEqual(expect.arrayContaining([...expected, 'nous-seedream', 'nous-moss-asr']));
    expect(chips).not.toContain('nous-deepseek');
  });

  it('the idle row is greyed the same way on every surface', async () => {
    getPlatformStatus.mockReturnValue(new Promise(() => {}));
    const text = renderHook(() => useTextPlatformModels());
    expect(text.result.current.find((m) => m.name === 'nous-qwen3-27b')?.status).toBe('idle');
    render(<AISettings settings={SETTINGS} onSave={vi.fn()} />);
    await waitFor(() =>
      expect(document.querySelector('option[value="nous:nous-qwen3-27b"]')).not.toBeNull(),
    );
    for (const o of Array.from(
      document.querySelectorAll<HTMLOptionElement>('option[value="nous:nous-qwen3-27b"]'),
    )) {
      expect(o.disabled).toBe(true);
    }
  });

  it('all surfaces together: ONE status request, ONE capability request, zero catalog requests', async () => {
    getPlatformStatus.mockResolvedValue(platformStatusWire({ 'nous-qwen3-8b': {} }));
    renderHook(() => useTextPlatformModels());
    renderHook(() => useTextPlatformModels());
    renderHook(() => useGenerationModels('image'));
    renderHook(() => useGenerationModels('video'));
    getAvailableModels(SETTINGS);
    render(<AISettings settings={SETTINGS} onSave={vi.fn()} />);
    await waitFor(() => expect(getPlatformStatus).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(listGenerationCapabilities).toHaveBeenCalledTimes(1));
    // Nothing else went to the network: no /ai/nous-models, no
    // /canvases/text-models, no /canvases/generation-models.
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
