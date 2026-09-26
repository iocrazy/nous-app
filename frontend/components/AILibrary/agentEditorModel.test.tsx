/**
 * renderModelSelect — the model picker's health marking.
 *
 * A model whose hourly probe is failing stays SELECTABLE on purpose: the probe
 * has been wrong before (2026-08-14, a model marked unreachable that worked
 * end-to-end), so the picker warns and lets the user decide. Disabling the
 * option would hand the probe a veto it hasn't earned.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  platformNotLoadedLabels,
  renderModelSelect,
  type ProviderModelGroup,
} from './agentEditorModel';
import { baseAISettings, withPlatform, type PlatformRowSpec } from '../../tests/fixtures/platform';

const GROUPS: ProviderModelGroup[] = [
  {
    providerKey: 'nous',
    providerName: 'Nous (Platform)',
    models: ['mediahub-deepseek-v4-flash', 'mediahub-deepseek-v4-pro'],
  },
];

// UiSelect renders the <option> children verbatim into a hidden native
// <select> (aria-hidden, so role queries can't reach them) and mirrors them
// into its own portal menu when opened. Querying the native options is the
// direct read of what this function produced.
let container: HTMLElement;

function renderSelect(over: Partial<Parameters<typeof renderModelSelect>[0]> = {}) {
  const result = render(
    renderModelSelect({
      value: 'mediahub-deepseek-v4-pro',
      groups: GROUPS,
      disabled: false,
      onChange: vi.fn(),
      providerNotEnabledLabel: 'provider not enabled',
      noModelsLabel: 'No models available',
      ...over,
    }),
  );
  container = result.container;
  return result;
}

const optionFor = (name: string): HTMLOptionElement => {
  const el = container.querySelector(`option[value="${name}"]`);
  if (!el) throw new Error(`no option for ${name}`);
  return el as HTMLOptionElement;
};

describe('renderModelSelect — health marking', () => {
  it('marks a failing model in its label', () => {
    renderSelect({
      unhealthyLabels: { 'mediahub-deepseek-v4-flash': 'health check failed, checked 20m ago' },
    });
    const flash = optionFor('mediahub-deepseek-v4-flash');
    expect(flash.textContent).toContain('health check failed');
    expect(flash.textContent).toContain('checked 20m ago');
    expect(flash.dataset.health).toBe('fail');
  });

  it('never disables the marked option — warn, do not stop', () => {
    renderSelect({
      unhealthyLabels: { 'mediahub-deepseek-v4-flash': 'health check failed' },
    });
    expect(optionFor('mediahub-deepseek-v4-flash').disabled).toBe(false);
  });

  it('leaves healthy models untouched', () => {
    renderSelect({
      unhealthyLabels: { 'mediahub-deepseek-v4-flash': 'health check failed' },
    });
    const pro = optionFor('mediahub-deepseek-v4-pro');
    expect(pro.textContent).toBe('mediahub-deepseek-v4-pro');
    expect(pro.dataset.health).toBeUndefined();
  });

  it('renders exactly as before when no health is known', () => {
    renderSelect();
    expect(optionFor('mediahub-deepseek-v4-flash').textContent).toBe(
      'mediahub-deepseek-v4-flash',
    );
    expect(container.querySelectorAll('option')).toHaveLength(2);
  });

  it('shows the warning on the closed trigger when the failing model is selected', () => {
    // The menu is closed nearly all the time — a marking only visible after
    // opening the dropdown would not reach the user who already picked it.
    renderSelect({
      value: 'mediahub-deepseek-v4-flash',
      unhealthyLabels: { 'mediahub-deepseek-v4-flash': 'health check failed' },
    });
    const trigger = screen.getByRole('button');
    expect(trigger.textContent).toContain('health check failed');
  });
});

// ── display names (user 2026-09-06: "名字不还是没有改吗?") ─────────────────
// The picker showed catalog ids (mediahub-deepseek-v4-pro) and prefixed card
// ids (codex:gpt-5.6-sol). The VALUE stays the id — that is what routing and
// the agent row store — but the visible text is the human name.
describe('renderModelSelect — labels', () => {
  it('shows a group-provided label while keeping the id as the value', () => {
    const groups: ProviderModelGroup[] = [
      {
        providerKey: 'nous',
        providerName: 'Nous (Platform)',
        models: ['mediahub-deepseek-v4-pro'],
        labels: { 'mediahub-deepseek-v4-pro': 'DeepSeek V4 Pro' },
      },
    ];
    renderSelect({ groups, value: 'mediahub-deepseek-v4-pro' });
    const opt = document.querySelector('option[value="mediahub-deepseek-v4-pro"]') as HTMLOptionElement;
    expect(opt.textContent).toBe('DeepSeek V4 Pro');
  });

  it('getAvailableModels labels the Codex card models without the codex: prefix', async () => {
    const { getAvailableModels } = await import('./agentEditorModel');
    const g = getAvailableModels({
      providers: { 'codex-local': { enabled: true, enabled_models: ['codex:gpt-5.6-sol'] } },
    } as never).find((x) => x.providerKey === 'codex-local');
    expect(g?.models).toEqual(['codex:gpt-5.6-sol']);
    expect(g?.labels).toEqual({ 'codex:gpt-5.6-sol': 'gpt-5.6-sol' });
  });
});

// ── platform rows obey the Providers page's Nous card (user 2026-09-06:
// "agent 也属于应用，可用模型取值也来源于唯一管理入口"). Since spec 2026-09-25 the
// card is computed server-side and has the BYOK shape, so getAvailableModels
// produces the platform group from the settings alone — no second request.
describe('getAvailableModels — the platform card', () => {
  const ROWS: PlatformRowSpec[] = [
    { name: 'nous-qwen3-8b', actual_model: 'qwen3-8b', type: 'llm' },
    { name: 'nous-wemm-2b', actual_model: 'wemm-2b', type: 'embedding' },
    { name: 'nous-deepseek', actual_model: 'deepseek-v4-pro', type: 'llm', disabled: true },
    { name: 'jimeng-local-image', actual_model: '', type: 'image', is_local: true },
  ];

  it('lists only enabled llm rows, labelled the way admin does, valued by row name', async () => {
    const { getAvailableModels } = await import('./agentEditorModel');
    const nous = getAvailableModels(withPlatform(baseAISettings(), ROWS)).find(
      (g) => g.providerKey === 'nous',
    );
    expect(nous?.models).toEqual(['nous-qwen3-8b']);
    expect(nous?.labels).toEqual({ 'nous-qwen3-8b': 'qwen3-8b' });
  });

  it('offers nothing when the Nous card itself is off', async () => {
    const { getAvailableModels } = await import('./agentEditorModel');
    const groups = getAvailableModels(withPlatform(baseAISettings(), ROWS, { enabled: false }));
    expect(groups.find((g) => g.providerKey === 'nous')).toBeUndefined();
  });

  it('offers nothing when the platform view is unknown (platform_models: null)', async () => {
    const { getAvailableModels } = await import('./agentEditorModel');
    const settings = { ...withPlatform(baseAISettings(), ROWS), platform_models: null };
    expect(getAvailableModels(settings).find((g) => g.providerKey === 'nous')).toBeUndefined();
  });
});

describe('renderModelSelect — non-chat note', () => {
  // The user's report (2026-09-15): a doubao card holding an embedding model,
  // an image model and a chat model is a CORRECT setup for this product. The
  // picker should say which is which — without implying anything is broken.
  const BYOK: ProviderModelGroup[] = [
    {
      providerKey: 'doubao',
      providerName: 'Doubao',
      models: ['doubao-seed-2-0-lite-260428', 'doubao-embedding-large-text-250515'],
    },
  ];

  it('marks a non-chat model without claiming a health failure', () => {
    renderSelect({
      value: 'doubao-seed-2-0-lite-260428',
      groups: BYOK,
      noteLabels: { 'doubao-embedding-large-text-250515': 'not a chat model' },
    });

    const embed = optionFor('doubao-embedding-large-text-250515');
    expect(embed.textContent).toContain('not a chat model');
    // `data-note`, NOT `data-health="fail"`. A BYOK model is never probed, so
    // saying its health check failed would be a claim about something that
    // never ran — and it is the DOM hook any styling would key on.
    expect(embed.dataset.note).toBe('non-chat');
    expect(embed.dataset.health).toBeUndefined();
    // Selectable, like every other marked option on this picker.
    expect(embed.disabled).toBe(false);
  });

  it('leaves ordinary chat models untouched', () => {
    renderSelect({
      value: 'doubao-seed-2-0-lite-260428',
      groups: BYOK,
      noteLabels: { 'doubao-embedding-large-text-250515': 'not a chat model' },
    });

    const chat = optionFor('doubao-seed-2-0-lite-260428');
    expect(chat.textContent).toBe('doubao-seed-2-0-lite-260428');
    expect(chat.dataset.note).toBeUndefined();
  });

  it('carries both suffixes when a model is failing AND non-chat', () => {
    // Can happen on a platform row: both maps are keyed by model name and
    // neither knows about the other, so the option must not drop one.
    renderSelect({
      unhealthyLabels: { 'mediahub-deepseek-v4-flash': 'health check failed' },
      noteLabels: { 'mediahub-deepseek-v4-flash': 'not a chat model' },
    });

    const flash = optionFor('mediahub-deepseek-v4-flash');
    expect(flash.textContent).toContain('health check failed');
    expect(flash.textContent).toContain('not a chat model');
    expect(flash.dataset.health).toBe('fail');
    expect(flash.dataset.note).toBe('non-chat');
  });
});

describe('renderModelSelect — orphan labels', () => {
  it('says "unavailable" for a saved model the caller knows was hidden', () => {
    renderSelect({
      value: 'nous-hidden',
      orphanLabels: { 'nous-hidden': 'hidden-model-id (unavailable)' },
    });
    const opt = document.querySelector('option[data-orphan="unavailable"]');
    expect(opt?.textContent).toBe('hidden-model-id (unavailable)');
    expect(screen.queryByText(/provider not enabled/)).toBeNull();
  });

  it('keeps the generic stale wording for any other orphan', () => {
    renderSelect({ value: 'gone-model', orphanLabels: {} });
    const opt = document.querySelector('option[data-orphan="stale"]');
    expect(opt?.textContent).toContain('gone-model');
  });
});

// ── idle platform rows (nous-engine authorized, not loaded) ─────────────────
// Unlike a failing probe (advice, stays selectable), an idle row answered a
// real chat with 503 "not loaded" on 2026-09-24 — picking it cannot work, so
// the option is visible but disabled, with the reason as its description.
describe('renderModelSelect — not loaded on nous-engine', () => {
  const NOT_LOADED = { 'mediahub-deepseek-v4-flash': 'Not loaded on nous-engine' };

  it('disables the idle option and carries the reason', () => {
    renderSelect({ notLoadedLabels: NOT_LOADED });
    const flash = optionFor('mediahub-deepseek-v4-flash');
    expect(flash.disabled).toBe(true);
    expect(flash.dataset.availability).toBe('not_loaded');
    expect(flash.dataset.description).toBe('Not loaded on nous-engine');
    expect(optionFor('mediahub-deepseek-v4-pro').disabled).toBe(false);
  });

  it('keeps an idle saved value shown, dimmed, with the reason on the trigger', () => {
    renderSelect({ value: 'mediahub-deepseek-v4-flash', notLoadedLabels: NOT_LOADED });
    const trigger = screen.getByRole('button');
    expect(trigger.textContent).toContain('mediahub-deepseek-v4-flash');
    expect(trigger.getAttribute('title')).toBe('Not loaded on nous-engine');
    // Not treated as an orphan: it is still a known row of its group.
    expect(container.querySelectorAll('option[data-orphan]')).toHaveLength(0);
  });
});

describe('platformNotLoadedLabels', () => {
  it('maps only idle rows to the reason', () => {
    const rows = [
      { name: 'a', status: 'idle' as const },
      { name: 'b', status: 'ok' as const },
      { name: 'c' },
      { name: 'd', status: 'not_probed' as const },
    ];
    expect(platformNotLoadedLabels(rows, 'Not loaded on nous-engine')).toEqual({
      a: 'Not loaded on nous-engine',
    });
  });
});
