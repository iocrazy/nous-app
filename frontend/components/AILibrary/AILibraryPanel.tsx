// frontend/components/AILibrary/AILibraryPanel.tsx
// Placeholder — Task 15 will implement full tab UI (Agents + Skills)
import React from 'react';
import { useTranslation } from 'react-i18next';

export const AILibraryPanel: React.FC = () => {
  const { t } = useTranslation();
  return (
    <section className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden animate-in fade-in duration-300">
      <div className="px-6 py-4 border-b border-zinc-800 bg-zinc-900/50">
        <h2 className="font-semibold text-zinc-200">{t('aiLibrary.title', 'AI Library')}</h2>
      </div>
      <div className="p-6 text-white/70">
        AI Library panel — implementation in Task 15.
      </div>
    </section>
  );
};

export default AILibraryPanel;
