// frontend/pages/MemoryPage.tsx
// AI Library → My Memory. The sidebar has pointed at /ai-library/memory
// since the nav was built, but the route never existed (dead link — clicks
// fell into the catch-all redirect). Both panels are self-contained; this
// page just gives them the missing address.

import React from 'react';
import { useTranslation } from 'react-i18next';
import { MemoryPanel } from '../components/MemoryPanel';
import { AgentMemoriesPanel } from '../components/AgentMemoriesPanel';
import { PageHeader } from '../components/AILibrary/PageHeader';

export const MemoryPage: React.FC = () => {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-3xl space-y-6 pb-12">
      <PageHeader title={t('sidebar.memory', 'My Memory')} className="pb-0" />
      {/* Phase 4: user-facing AI memory management (Claude-style) */}
      <MemoryPanel />
      {/* Agent memories: facts/decisions the AI curated (own + team-shared) */}
      <AgentMemoriesPanel />
    </div>
  );
};

export default MemoryPage;
