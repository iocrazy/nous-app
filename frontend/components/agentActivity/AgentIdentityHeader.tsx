/**
 * Agent identity in the chat panel header (design §D).
 *
 * Replaces the old "AI Chat" + bare "No agent" pairing: the user should be
 * able to tell at a glance WHO they are talking to and WHAT that agent does,
 * because with tools in play the answer now changes what happens to their
 * script.
 */

import React from 'react';
import { useTranslation } from 'react-i18next';

import { getAgentIcon } from '../AILibrary/agentIcons';

export interface AgentIdentityHeaderProps {
  name?: string | null;
  description?: string | null;
  /** Lucide slug from ai_agents.icon; falls back to Bot. */
  icon?: string | null;
  /** The agent picker, rendered inline so identity and switching sit together. */
  action?: React.ReactNode;
}

export function AgentIdentityHeader({
  name,
  description,
  icon,
  action,
}: AgentIdentityHeaderProps): React.ReactElement {
  const { t } = useTranslation();
  const Icon = getAgentIcon(icon ?? null);
  const hasAgent = Boolean(name);

  return (
    <div className="flex min-w-0 flex-1 items-center gap-2" data-testid="agent-identity">
      <span
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border ${
          hasAgent
            ? 'border-agent-line bg-agent-soft text-agent'
            : 'border-ink-700 bg-ink-800 text-ink-500'
        }`}
      >
        <Icon size={14} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col leading-tight">
        <span className="truncate text-sm font-medium text-ink-200">
          {hasAgent ? name : t('agentActivity.chooseAgent', 'Choose an agent')}
        </span>
        <span className="truncate text-[11px] text-ink-500">
          {hasAgent
            ? (description ?? t('agentActivity.noDescription', 'No description'))
            : t('agentActivity.chooseAgentHint', 'Pick who you want to work with')}
        </span>
      </span>
      {action}
    </div>
  );
}
