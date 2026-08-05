/**
 * Context-appropriate quick actions above the composer (design §D).
 *
 * The set changes with what is actually on screen, which is the whole point:
 * "Tighten this exchange" is meaningless without a selection, and "Draft a
 * shot list for this scene" is noise on a general chat. Each action seeds the
 * composer rather than sending — the user still gets the last word before an
 * agent with write tools acts on their script.
 */

import React from 'react';
import { useTranslation } from 'react-i18next';

/** Which situation the panel is in — decides the offered actions. */
export type QuickActionContext = 'selection' | 'script' | 'none';

interface QuickActionDef {
  /** i18n key suffix under agentActivity.quick. */
  id: string;
  /** English default; the prompt text seeded into the composer. */
  prompt: string;
}

const ACTIONS: Record<QuickActionContext, QuickActionDef[]> = {
  selection: [
    { id: 'tighten', prompt: 'Tighten this passage without losing its meaning.' },
    { id: 'alternatives', prompt: 'Give me two alternative versions of this passage.' },
    { id: 'toShots', prompt: 'Break this passage into shot cards.' },
  ],
  script: [
    { id: 'listScenes', prompt: 'List the scenes in this episode and flag the weak ones.' },
    { id: 'shotList', prompt: 'Draft a shot list for the scene I am on.' },
    { id: 'continuity', prompt: 'Check this episode for continuity problems.' },
  ],
  none: [],
};

export interface QuickActionsProps {
  context: QuickActionContext;
  onPick: (prompt: string) => void;
}

export function QuickActions({
  context,
  onPick,
}: QuickActionsProps): React.ReactElement | null {
  const { t } = useTranslation();
  const actions = ACTIONS[context] ?? [];
  if (actions.length === 0) return null;

  return (
    <div
      className="flex flex-wrap gap-1.5 px-3 pb-1.5"
      data-testid="quick-actions"
      data-context={context}
    >
      {actions.map((action) => {
        const label = t(`agentActivity.quick.${action.id}`, action.prompt);
        return (
          <button
            key={action.id}
            type="button"
            onClick={() => onPick(label)}
            data-testid="quick-action"
            data-action={action.id}
            className="rounded-full border border-ink-700 px-2 py-0.5 text-[11px] text-ink-400 transition-colors hover:border-agent-line hover:bg-agent-soft hover:text-agent"
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
