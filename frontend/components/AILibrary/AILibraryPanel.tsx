// frontend/components/AILibrary/AILibraryPanel.tsx
// Top-level AI Library panel shown inside SettingsModal.
// Two tabs: Agents (list + editor) and Skills (card grid — Task 17 will expand).

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AgentsTab } from './AgentsTab';
import { SkillsTab } from './SkillsTab';

type Tab = 'agents' | 'skills';

export const AILibraryPanel: React.FC = () => {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('agents');

  const tabs: Tab[] = ['agents', 'skills'];

  return (
    <section className="flex h-full flex-col bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden animate-in fade-in duration-300">
      <header className="px-6 py-4 border-b border-zinc-800 bg-zinc-900/50">
        <h2 className="font-semibold text-zinc-200">{t('aiLibrary.title')}</h2>
      </header>
      <nav className="flex gap-1 border-b border-zinc-800 px-6 py-2 bg-zinc-900/30">
        {tabs.map((key) => {
          const active = tab === key;
          return (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                active
                  ? 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/30'
                  : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50 border border-transparent'
              }`}
            >
              {t(`aiLibrary.tabs.${key}`)}
            </button>
          );
        })}
      </nav>
      <div className="flex-1 overflow-hidden">
        {tab === 'agents' ? <AgentsTab /> : <SkillsTab />}
      </div>
    </section>
  );
};

export default AILibraryPanel;
