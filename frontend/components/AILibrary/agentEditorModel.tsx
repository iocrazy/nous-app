// frontend/components/AILibrary/agentEditorModel.tsx
// Model-picker helpers shared by the agent editor's tabs.
//
// Lifted out of AgentEditor when the 8 sub-tabs collapsed into three (B2,
// spec 2026-08-02 §B2): the persona tab renders the picker, while the tests
// exercise getAvailableModels on its own. Behaviour is unchanged — this is a
// move, not a rewrite.

import React from 'react';

import { suspectedNonChatKind } from '../../utils/nonChatModel';
import { isPlatformModelAvailable } from '../../utils/platformModel';
import type {
  AIProviderConfig,
  AISettings as AISettingsType,
} from '../../types';
import type { NousModelPublic } from '../../types/api';
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
 *
 * Rows whose last probe FAILED are dropped regardless of config (2026-09-24,
 * utils/platformModel): the admin page shows them as broken, so the user side
 * does not offer them. ``not_probed`` / never-probed rows stay.
 */
export function visiblePlatformModels<
  T extends { name: string; last_test_status?: NousModelPublic['last_test_status'] },
>(rows: T[], nousConfig: AIProviderConfig | undefined): T[] {
  const available = rows.filter(isPlatformModelAvailable);
  if (!nousConfig) return available;
  if (nousConfig.enabled === false) return [];
  const disabled = new Set(nousConfig.disabled_models ?? []);
  return available.filter((m) => !disabled.has(m.name));
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
 * Which of these models look like they cannot hold a chat, mapped to the
 * caller's already-localized note.
 *
 * The picker lists every enabled model of every enabled provider, so an
 * embedding or image model a user legitimately keeps on the same provider card
 * shows up here too. Naming the family is the whole fix — the option stays
 * selectable, because the kind is a guess from the id string and nothing more
 * (utils/nonChatModel explains why there is nothing better to read).
 *
 * ⚠️ Platform (`nous`) rows are skipped, and that exclusion is the reason this
 * is a function rather than three lines at the call site. A platform row's
 * `name` is an admin-chosen ALIAS, not an upstream model id: `codex-image` is
 * a real catalog row whose `actual_model` is the chat model gpt-5.4 (mig 430).
 * Running the name heuristic over those would mark a working chat model as
 * non-chat — precisely the false alarm this whole treatment exists to undo.
 */
export function nonChatModelNotes(
  groups: ProviderModelGroup[],
  note: string,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const group of groups) {
    if (group.providerKey === 'nous') continue;
    for (const m of group.models) {
      if (suspectedNonChatKind(m)) out[m] = note;
    }
  }
  return out;
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
 *
 * ``orphanLabels`` overrides the text of that leading stale option for a value
 * the caller knows more about — a platform model hidden because its probe
 * failed is "unavailable", not "provider not enabled".
 *
 * ``noteLabels`` is the same shape for a NON-alarming remark — today, "this id
 * looks like an embedding/image model". It is a separate map rather than a
 * second kind of entry in ``unhealthyLabels`` so the DOM never claims a health
 * probe failed on a model that was never probed: these options carry
 * ``data-note``, not ``data-health="fail"``. Both are suffixes, both stay
 * selectable, and an option can carry one of each.
 */
export function renderModelSelect(params: {
  value: string;
  groups: ProviderModelGroup[];
  disabled: boolean;
  onChange: (v: string) => void;
  providerNotEnabledLabel: string;
  noModelsLabel: string;
  unhealthyLabels?: Record<string, string>;
  noteLabels?: Record<string, string>;
  orphanLabels?: Record<string, string>;
}): React.ReactElement {
  const {
    value,
    groups,
    disabled,
    onChange,
    providerNotEnabledLabel,
    noModelsLabel,
    unhealthyLabels,
    noteLabels,
    orphanLabels,
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
        <option value={value} data-orphan={orphanLabels?.[value] ? 'unavailable' : 'stale'}>
          {orphanLabels?.[value] ?? `${value} (${providerNotEnabledLabel})`}
        </option>
      )}
      {groups.length === 0 && !showOrphan && (
        <option value="">{noModelsLabel}</option>
      )}
      {groups.map((group) => (
        <optgroup key={group.providerKey} label={group.providerName}>
          {group.models.map((m) => {
            const warning = unhealthyLabels?.[m];
            const note = noteLabels?.[m];
            const label = group.labels?.[m] ?? m;
            const suffixes = [warning, note].filter(Boolean);
            return (
              <option
                key={`${group.providerKey}:${m}`}
                value={m}
                data-health={warning ? 'fail' : undefined}
                data-note={note ? 'non-chat' : undefined}
              >
                {suffixes.length > 0
                  ? `${label} — ${suffixes.join(' · ')}`
                  : label}
              </option>
            );
          })}
        </optgroup>
      ))}
    </UiSelect>
  );
}
