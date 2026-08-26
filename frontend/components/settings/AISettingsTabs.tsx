// components/settings/AISettingsTabs.tsx
//
// The AI settings area with SECOND-LEVEL tabs (user correction 2026-08-26:
// Providers / Local CLI / MCP / Memory belong INSIDE the AI tab, not as
// top-level sidebar entries). One sidebar entry — five sub-views.

import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { AISettings as AISettingsType } from '../../types';
import AISettings from '../AISettings';
import { AgentMemoriesPanel } from '../AgentMemoriesPanel';
import { MCPServersPanel } from '../MCPServersPanel';
import { MemoryPanel } from '../MemoryPanel';
import { LocalCliSettings } from './LocalCliSettings';

type AISubTab = 'general' | 'providers' | 'local-cli' | 'mcp' | 'memory';

const SUB_TABS: Array<{ id: AISubTab; labelKey: string }> = [
  { id: 'general', labelKey: 'settings.aiTabs.general' },
  { id: 'providers', labelKey: 'settings.aiTabs.providers' },
  { id: 'local-cli', labelKey: 'settings.aiTabs.localCli' },
  { id: 'mcp', labelKey: 'settings.aiTabs.mcp' },
  { id: 'memory', labelKey: 'settings.aiTabs.memory' },
];

export function AISettingsTabs({
  settings,
  onSave,
}: {
  settings: AISettingsType;
  onSave: (settings: AISettingsType) => void;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<AISubTab>('general');
  return (
    <div className="space-y-6">
      <div
        className="flex gap-1 rounded-xl bg-ink-900 border border-ink-800 p-1"
        data-testid="ai-subtabs"
      >
        {SUB_TABS.map((st) => (
          <button
            key={st.id}
            type="button"
            data-testid={`ai-subtab-${st.id}`}
            onClick={() => setTab(st.id)}
            className={`flex-1 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === st.id
                ? 'bg-ink-800 text-ink-100'
                : 'text-ink-400 hover:text-ink-200'
            }`}
          >
            {t(st.labelKey)}
          </button>
        ))}
      </div>

      {tab === 'general' && (
        <AISettings settings={settings} onSave={onSave} section="core" />
      )}
      {tab === 'providers' && (
        <AISettings settings={settings} onSave={onSave} section="providers" />
      )}
      {tab === 'local-cli' && <LocalCliSettings />}
      {tab === 'mcp' && (
        <section className="bg-ink-900/40 border border-ink-800 rounded-lg overflow-hidden">
          <MCPServersPanel />
        </section>
      )}
      {tab === 'memory' && (
        <div className="space-y-6">
          <MemoryPanel />
          <AgentMemoriesPanel />
        </div>
      )}
    </div>
  );
}
