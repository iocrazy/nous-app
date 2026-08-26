// components/settings/AISettingsTabs.tsx
//
// The AI settings area with SECOND-LEVEL tabs (user correction 2026-08-26:
// Providers / Local CLI / MCP / Memory belong INSIDE the AI tab, not as
// top-level sidebar entries). One sidebar entry — five sub-views.

import { useState } from 'react';

import type { AISettings as AISettingsType } from '../../types';
import AISettings from '../AISettings';
import { AgentMemoriesPanel } from '../AgentMemoriesPanel';
import { MCPServersPanel } from '../MCPServersPanel';
import { MemoryPanel } from '../MemoryPanel';
import { LocalCliSettings } from './LocalCliSettings';

type AISubTab = 'general' | 'providers' | 'local-cli' | 'mcp' | 'memory';

const SUB_TABS: Array<{ id: AISubTab; label: string }> = [
  { id: 'general', label: 'General' },
  { id: 'providers', label: 'Providers' },
  { id: 'local-cli', label: 'Local CLI' },
  { id: 'mcp', label: 'MCP' },
  { id: 'memory', label: 'Memory' },
];

export function AISettingsTabs({
  settings,
  onSave,
}: {
  settings: AISettingsType;
  onSave: (settings: AISettingsType) => void;
}) {
  const [tab, setTab] = useState<AISubTab>('general');
  return (
    <div className="space-y-6">
      <div
        className="flex gap-1 rounded-xl bg-ink-900 border border-ink-800 p-1"
        data-testid="ai-subtabs"
      >
        {SUB_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            data-testid={`ai-subtab-${t.id}`}
            onClick={() => setTab(t.id)}
            className={`flex-1 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === t.id
                ? 'bg-ink-800 text-ink-100'
                : 'text-ink-400 hover:text-ink-200'
            }`}
          >
            {t.label}
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
