// frontend/components/AILibrary/agentEditorModel.tsx
// Model-picker helpers shared by the agent editor's tabs.
//
// Lifted out of AgentEditor when the 8 sub-tabs collapsed into three (B2,
// spec 2026-08-02 §B2): the persona tab renders the picker, while the tests
// exercise getAvailableModels on its own. Behaviour is unchanged — this is a
// move, not a rewrite.

import React from 'react';
import type { AIProviderConfig, AISettings as AISettingsType } from '../../types';
import { UiSelect } from '../ui';

/**
 * Model group — one entry per enabled provider, with its available models.
 * Used to render grouped <optgroup> in the model picker.
 */
export interface ProviderModelGroup {
  providerKey: string;
  providerName: string;
  models: string[];
  /** Human label per model id. The VALUE stays the id — routing and the
   *  agent row store ids — only the visible text changes. Missing → the id. */
  labels?: Record<string, string>;
}

// Friendly names for providers when the Overview model picker renders optgroups.
// Keep in sync with AISettings.tsx PROVIDER_META (we don't import from there to
// avoid a circular-ish dependency; this mapping is small and stable).
export const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  openai: 'OpenAI',
  'codex-local': 'Codex (Local CLI)',
  deepseek: 'DeepSeek',
  doubao: 'Doubao',
  minimax: 'MiniMax',
  kimi: 'Kimi',
  qwen: 'Qwen',
  volcengine: 'Volcengine',
  ollama: 'Ollama',
  lmstudio: 'LM Studio',
  nous: 'Nous (Platform)',
};

/**
 * Platform (Nous) rows the user may actually pick, per the Nous card on the
 * Providers page — the ONE management entry (2026-09-06). ``undefined`` config
 * = the user never touched the card = no restriction; ``enabled === false``
 * = nothing; otherwise drop the rows listed in ``disabled_models``.
 */
export function visiblePlatformModels<T extends { name: string }>(
  rows: T[],
  nousConfig: AIProviderConfig | undefined,
): T[] {
  if (!nousConfig) return rows;
  if (nousConfig.enabled === false) return [];
  const disabled = new Set(nousConfig.disabled_models ?? []);
  return rows.filter((m) => !disabled.has(m.name));
}

/**
 * Collect the curated whitelist of models exposed by every enabled
 * provider in aiSettings. Reads ``config.enabled_models`` (set in the
 * AI Settings UI via "Add Model" chips) — this is intentionally narrow
 * so users see only the models they care about, not the full 100+
 * provider catalog.
 *
 * Falls back to ``[selected_model]`` when ``enabled_models`` is missing
 * (legacy accounts; aiService.getAISettings auto-seeds this on read).
 */
export function getAvailableModels(
  settings: AISettingsType | null | undefined,
): ProviderModelGroup[] {
  if (!settings?.providers) return [];
  return Object.entries(settings.providers)
    .filter(([, config]) => config?.enabled)
    .map(([key, config]) => {
      const whitelist = config?.enabled_models;
      const fallback = config?.selected_model ? [config.selected_model] : [];
      const models = Array.from(new Set(whitelist ?? fallback)).filter(Boolean);
      // The Codex card's ids carry a ``codex:`` routing prefix (backend
      // services/codex/provider_card.py); the group already says Codex, so
      // the label drops it.
      const labels =
        key === 'codex-local'
          ? Object.fromEntries(models.map((m) => [m, m.replace(/^codex:/i, '')]))
          : undefined;
      return {
        providerKey: key,
        providerName: PROVIDER_DISPLAY_NAMES[key] ?? key,
        models,
        ...(labels ? { labels } : {}),
      };
    })
    .filter((g) => g.models.length > 0);
}

/**
 * Render the grouped model <select>. If the current value is not present in
 * any provider group (e.g. the user has disabled the provider that owned it),
 * we still show it as a leading disabled option so the user sees the stale
 * selection rather than it silently flipping to the first option.
 *
 * ``unhealthyLabels`` maps a model name to an already-localized warning suffix
 * (built by the caller, so this module stays free of i18n). A marked option is
 * still SELECTABLE: the health probe has produced a false negative in
 * production, so it advises rather than vetoes.
 */
export function renderModelSelect(params: {
  value: string;
  groups: ProviderModelGroup[];
  disabled: boolean;
  onChange: (v: string) => void;
  providerNotEnabledLabel: string;
  noModelsLabel: string;
  unhealthyLabels?: Record<string, string>;
}): React.ReactElement {
  const {
    value,
    groups,
    disabled,
    onChange,
    providerNotEnabledLabel,
    noModelsLabel,
    unhealthyLabels,
  } = params;
  const knownModels = new Set(groups.flatMap((g) => g.models));
  const showOrphan = value !== '' && !knownModels.has(value);

  return (
    <UiSelect
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className="mt-1 w-full font-mono"
    >
      {showOrphan && (
        <option value={value}>
          {value} ({providerNotEnabledLabel})
        </option>
      )}
      {groups.length === 0 && !showOrphan && (
        <option value="">{noModelsLabel}</option>
      )}
      {groups.map((group) => (
        <optgroup key={group.providerKey} label={group.providerName}>
          {group.models.map((m) => {
            const warning = unhealthyLabels?.[m];
            const label = group.labels?.[m] ?? m;
            return (
              <option
                key={`${group.providerKey}:${m}`}
                value={m}
                data-health={warning ? 'fail' : undefined}
              >
                {warning ? `${label} — ${warning}` : label}
              </option>
            );
          })}
        </optgroup>
      ))}
    </UiSelect>
  );
}
