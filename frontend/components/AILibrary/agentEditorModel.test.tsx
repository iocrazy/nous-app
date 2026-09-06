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
import { renderModelSelect, type ProviderModelGroup } from './agentEditorModel';

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
// "agent 也属于应用，可用模型取值也来源于唯一管理入口") ────────────────────────
describe('visiblePlatformModels', () => {
  const rows = [{ name: 'a' }, { name: 'b' }] as never[];
  it('drops rows the user switched off on the Nous card', async () => {
    const { visiblePlatformModels } = await import('./agentEditorModel');
    expect(visiblePlatformModels(rows, { enabled: true, disabled_models: ['b'] } as never).map((m: { name: string }) => m.name)).toEqual(['a']);
  });
  it('offers nothing when the Nous card itself is off', async () => {
    const { visiblePlatformModels } = await import('./agentEditorModel');
    expect(visiblePlatformModels(rows, { enabled: false } as never)).toEqual([]);
  });
  it('an absent card config means no restriction', async () => {
    const { visiblePlatformModels } = await import('./agentEditorModel');
    expect(visiblePlatformModels(rows, undefined)).toHaveLength(2);
  });
});
